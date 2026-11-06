#!/usr/bin/env python3
"""
train_lora.py — Offline LoRA Fine-Tuning Pipeline for G-Code Generation
=========================================================================
Reads exported JSONL datasets (from export_dataset.py), runs LoRA fine-tuning
on a local Qwen2.5-Coder or DeepSeek-Coder model, saves the adapter weights,
and optionally hot-reloads the Ollama inference engine with the new adapter.

Requirements (On-Prem only):
    pip install torch transformers peft accelerate datasets trl bitsandbytes
    # Optional for faster training:
    pip install unsloth

Usage:
    python scripts/train_lora.py \\
        --data ./data/training/gcode_diffs_*.jsonl \\
        --base-model Qwen/Qwen2.5-Coder-7B-Instruct \\
        --output ./models/lora_adapters/gcode_v1 \\
        --epochs 3 \\
        --batch-size 2

Environment:
    DATABASE_URL    — PostgreSQL URL for job status updates
    LOCAL_LLM_URL   — Ollama API URL for hot reload
"""

import os
import sys
import json
import glob
import argparse
import time
from pathlib import Path
from datetime import datetime

# ─── Soft dependency check ────────────────────────────────────────────────────
def check_deps():
    missing = []
    for pkg in ["torch", "transformers", "peft", "datasets", "trl"]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"ERROR: Missing packages: {', '.join(missing)}")
        print("Install with: pip install torch transformers peft datasets trl bitsandbytes accelerate")
        sys.exit(1)

# ─── Job Tracker (PostgreSQL) ─────────────────────────────────────────────────
class JobTracker:
    def __init__(self, db_url: str):
        self.db_url = db_url
        self.job_id = None
        try:
            import psycopg2
            self.conn = psycopg2.connect(db_url)
        except Exception:
            self.conn = None

    def start(self, base_model: str, dataset_size: int, num_epochs: int) -> str:
        if not self.conn:
            return "local"
        try:
            cur = self.conn.cursor()
            cur.execute(
                """INSERT INTO training_jobs (status, base_model, dataset_size, num_epochs, started_at)
                   VALUES ('RUNNING', %s, %s, %s, now()) RETURNING id""",
                (base_model, dataset_size, num_epochs)
            )
            self.job_id = str(cur.fetchone()[0])
            self.conn.commit()
            print(f"[TrainLoRA] Job ID: {self.job_id}")
            return self.job_id
        except Exception as e:
            print(f"[TrainLoRA] DB job tracking error: {e}")
            return "local"

    def update(self, status: str, loss: float = None, error: str = None, adapter_path: str = None):
        if not self.conn or not self.job_id:
            return
        try:
            cur = self.conn.cursor()
            updates = ["status = %s"]
            vals    = [status]
            if status in ("COMPLETE", "FAILED"):
                updates.append("completed_at = now()")
            if loss is not None:
                updates.append("loss_final = %s"); vals.append(loss)
            if error:
                updates.append("error_message = %s"); vals.append(error[:2000])
            if adapter_path:
                updates.append("adapter_path = %s"); vals.append(adapter_path)
            vals.append(self.job_id)
            cur.execute(f"UPDATE training_jobs SET {', '.join(updates)} WHERE id = %s::uuid", vals)
            self.conn.commit()
        except Exception as e:
            print(f"[TrainLoRA] DB update error: {e}")


# ─── Dataset Loader ───────────────────────────────────────────────────────────
def load_jsonl_files(patterns: list[str]) -> list[dict]:
    records = []
    for pattern in patterns:
        for path in glob.glob(pattern):
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
    return records


def format_alpaca_prompt(sample: dict) -> str:
    """Convert Alpaca sample to single training string."""
    instruction = sample.get("instruction", "")
    inp         = sample.get("input", "")
    output      = sample.get("output", "")
    if inp:
        return f"### Instruction:\n{instruction}\n\n### Input:\n{inp}\n\n### Response:\n{output}"
    return f"### Instruction:\n{instruction}\n\n### Response:\n{output}"


def format_sharegpt_prompt(sample: dict, tokenizer) -> list[dict]:
    """Return conversation list for chat_template formatting."""
    messages = []
    if sample.get("system"):
        messages.append({"role": "system", "content": sample["system"]})
    for turn in sample.get("conversations", []):
        role = "user" if turn["from"] == "human" else "assistant"
        messages.append({"role": role, "content": turn["value"]})
    return messages


# ─── Main Training Loop ───────────────────────────────────────────────────────
def train(
    data_patterns:  list[str],
    base_model:     str,
    output_dir:     str,
    num_epochs:     int = 3,
    batch_size:     int = 2,
    grad_accum:     int = 4,
    max_seq_len:    int = 2048,
    lora_rank:      int = 16,
    lora_alpha:     int = 32,
    lora_dropout:   float = 0.05,
    learning_rate:  float = 2e-4,
    use_4bit:       bool = True,
    db_url:         str = None,
):
    check_deps()

    import torch
    from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig, TrainingArguments
    from peft import LoraConfig, get_peft_model, TaskType
    from datasets import Dataset
    from trl import SFTTrainer

    tracker = JobTracker(db_url or os.environ.get("DATABASE_URL", ""))

    print(f"[TrainLoRA] Loading dataset from: {data_patterns}")
    records = load_jsonl_files(data_patterns)
    if not records:
        print("ERROR: No training data found. Run export_dataset.py first.")
        sys.exit(1)

    print(f"[TrainLoRA] {len(records)} training samples loaded")
    job_id = tracker.start(base_model, len(records), num_epochs)

    # Determine format
    fmt = "sharegpt" if "conversations" in records[0] else "alpaca"
    print(f"[TrainLoRA] Dataset format: {fmt}")

    try:
        # ── Tokenizer ──────────────────────────────────────────────────────────
        print(f"[TrainLoRA] Loading tokenizer: {base_model}")
        tokenizer = AutoTokenizer.from_pretrained(base_model, trust_remote_code=True)
        tokenizer.pad_token    = tokenizer.eos_token
        tokenizer.padding_side = "right"

        # ── Model with optional 4-bit quantization ─────────────────────────────
        bnb_config = None
        if use_4bit and torch.cuda.is_available():
            bnb_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.bfloat16,
                bnb_4bit_use_double_quant=True,
            )

        print(f"[TrainLoRA] Loading base model: {base_model} (4-bit={use_4bit})")
        model = AutoModelForCausalLM.from_pretrained(
            base_model,
            quantization_config=bnb_config,
            device_map="auto",
            trust_remote_code=True,
            torch_dtype=torch.bfloat16,
        )
        model.config.use_cache = False

        # ── LoRA Config ────────────────────────────────────────────────────────
        lora_config = LoraConfig(
            task_type=TaskType.CAUSAL_LM,
            r=lora_rank,
            lora_alpha=lora_alpha,
            lora_dropout=lora_dropout,
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
            bias="none",
        )
        model = get_peft_model(model, lora_config)
        model.print_trainable_parameters()

        # ── Format Dataset ─────────────────────────────────────────────────────
        if fmt == "alpaca":
            texts = [format_alpaca_prompt(r) for r in records]
            hf_dataset = Dataset.from_dict({"text": texts})
        else:
            # ShareGPT: apply chat template
            def apply_chat(sample):
                msgs    = format_sharegpt_prompt(sample, tokenizer)
                text    = tokenizer.apply_chat_template(msgs, tokenize=False, add_generation_prompt=False)
                return {"text": text}
            hf_dataset = Dataset.from_list(records).map(apply_chat, remove_columns=records[0].keys())

        # ── Training Arguments ─────────────────────────────────────────────────
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        args = TrainingArguments(
            output_dir=str(out_path),
            num_train_epochs=num_epochs,
            per_device_train_batch_size=batch_size,
            gradient_accumulation_steps=grad_accum,
            learning_rate=learning_rate,
            fp16=not torch.cuda.is_bf16_supported(),
            bf16=torch.cuda.is_bf16_supported(),
            logging_steps=10,
            save_steps=100,
            save_total_limit=2,
            warmup_ratio=0.05,
            lr_scheduler_type="cosine",
            report_to="none",
            dataloader_num_workers=0,
        )

        trainer = SFTTrainer(
            model=model,
            args=args,
            train_dataset=hf_dataset,
            dataset_text_field="text",
            max_seq_length=max_seq_len,
            tokenizer=tokenizer,
        )

        print("[TrainLoRA] Starting training…")
        train_result = trainer.train()
        final_loss = train_result.training_loss

        # ── Save Adapter ───────────────────────────────────────────────────────
        adapter_path = str(out_path / "final_adapter")
        trainer.model.save_pretrained(adapter_path)
        tokenizer.save_pretrained(adapter_path)

        # Write manifest
        manifest = {
            "base_model":    base_model,
            "adapter_path":  adapter_path,
            "dataset_size":  len(records),
            "num_epochs":    num_epochs,
            "final_loss":    final_loss,
            "lora_rank":     lora_rank,
            "trained_at":    datetime.now().isoformat(),
            "job_id":        job_id,
        }
        with open(out_path / "manifest.json", "w") as f:
            json.dump(manifest, f, indent=2)

        print(f"[TrainLoRA] Training complete. Loss: {final_loss:.4f}")
        print(f"[TrainLoRA] Adapter saved: {adapter_path}")

        tracker.update("COMPLETE", loss=final_loss, adapter_path=adapter_path)
        return manifest

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"[TrainLoRA] ERROR: {e}\n{tb}")
        tracker.update("FAILED", error=str(e))
        sys.exit(1)


# ─── Ollama Hot Reload ────────────────────────────────────────────────────────
def ollama_reload(base_model: str, adapter_path: str, ollama_url: str):
    """
    Creates an Ollama Modelfile that references the base model + LoRA adapter,
    then calls `ollama create` to register it as a new model variant.
    """
    import subprocess
    modelfile_content = f"""FROM {base_model}
ADAPTER {adapter_path}
PARAMETER temperature 0.1
PARAMETER num_ctx 4096
SYSTEM You are an expert CNC programmer. Generate machine-ready Fanuc/Haas G-code. Output ONLY G-code.
"""
    modelfile_path = Path(adapter_path) / "Modelfile"
    modelfile_path.write_text(modelfile_content)

    model_name = f"gcode-lora-{int(time.time())}"
    result = subprocess.run(
        ["ollama", "create", model_name, "-f", str(modelfile_path)],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(f"[TrainLoRA] Ollama model registered: {model_name}")
        return model_name
    else:
        print(f"[TrainLoRA] Ollama reload failed: {result.stderr}")
        return None


# ─── Entry Point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LoRA fine-tuning on human-approved G-code diffs")
    parser.add_argument("--data",       nargs="+", required=True,                      help="JSONL file patterns")
    parser.add_argument("--base-model", default="Qwen/Qwen2.5-Coder-7B-Instruct",      help="HuggingFace base model ID")
    parser.add_argument("--output",     default="./models/lora_adapters/gcode_lora",   help="Output directory for adapter")
    parser.add_argument("--epochs",     default=3,   type=int)
    parser.add_argument("--batch-size", default=2,   type=int)
    parser.add_argument("--grad-accum", default=4,   type=int)
    parser.add_argument("--rank",       default=16,  type=int,   help="LoRA rank")
    parser.add_argument("--lr",         default=2e-4, type=float)
    parser.add_argument("--no-4bit",    action="store_true",     help="Disable 4-bit quantization")
    parser.add_argument("--ollama-reload", action="store_true",  help="Register adapter with Ollama after training")
    args = parser.parse_args()

    manifest = train(
        data_patterns=args.data,
        base_model=args.base_model,
        output_dir=args.output,
        num_epochs=args.epochs,
        batch_size=args.batch_size,
        grad_accum=args.grad_accum,
        lora_rank=args.rank,
        learning_rate=args.lr,
        use_4bit=not args.no_4bit,
    )

    if args.ollama_reload:
        ollama_url = os.environ.get("LOCAL_LLM_URL", "http://localhost:11434")
        ollama_reload(args.base_model, manifest["adapter_path"], ollama_url)
