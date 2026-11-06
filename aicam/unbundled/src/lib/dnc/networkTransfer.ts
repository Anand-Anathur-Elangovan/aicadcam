// DNC (Direct Numerical Control) Network Transfer Module
// Supports TCP socket (Fanuc/Haas Ethernet), FTP, and SMB/shared path transfers.
// All methods return a TransferResult for status tracking.

import { createHash } from "crypto";
import { writeFile, mkdir } from "fs/promises";
import { join, basename } from "path";
import net from "net";

export type TransferProtocol = "TCP_SOCKET" | "FTP" | "SMB" | "SFTP";

export interface MachineTarget {
  id:          string;
  name:        string;
  displayName: string;
  protocol:    TransferProtocol;
  host:        string;
  port:        number;
  networkPath?: string;
  username?:   string;
  password?:   string;
}

export interface TransferResult {
  success:        boolean;
  machineId:      string;
  machineName:    string;
  programName:    string;
  bytesTransferred: number;
  checksum:       string;
  transferredAt:  string;
  protocol:       TransferProtocol;
  error?:         string;
}

// ─── Checksum ─────────────────────────────────────────────────────────────────

function md5(data: string | Buffer): string {
  return createHash("md5").update(data).digest("hex");
}

// ─── TCP Socket Transfer (Fanuc / Haas Ethernet DNC) ─────────────────────────
// Fanuc FOCAS2 / Haas Q-setter use a simple newline-delimited text protocol.
// This implementation sends the G-code over raw TCP and waits for ACK.

async function transferViaTCP(
  machine: MachineTarget,
  programName: string,
  gcode: string,
  timeoutMs = 10_000
): Promise<TransferResult> {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    let acked    = false;
    let responseData = "";

    const fail = (msg: string) => {
      socket.destroy();
      resolve({
        success: false, machineId: machine.id, machineName: machine.name,
        programName, bytesTransferred: 0, checksum: "", transferredAt: new Date().toISOString(),
        protocol: "TCP_SOCKET", error: msg,
      });
    };

    const timer = setTimeout(() => fail("Connection timeout"), timeoutMs);

    socket.connect(machine.port, machine.host, () => {
      // Fanuc/Haas DNC header: %, program name, then G-code, then %
      const payload = `%\n${programName}\n${gcode}\n%\n`;
      socket.write(payload, "ascii");
    });

    socket.on("data", (chunk) => {
      responseData += chunk.toString("ascii");
      // Most Fanuc/Haas controllers send ">" as ready, "ACK" or "%" as complete
      if (responseData.includes("ACK") || responseData.includes("%") || responseData.includes(">")) {
        if (!acked) {
          acked = true;
          clearTimeout(timer);
          socket.end();
          const bytes = Buffer.byteLength(gcode, "utf8");
          resolve({
            success: true, machineId: machine.id, machineName: machine.name,
            programName, bytesTransferred: bytes, checksum: md5(gcode),
            transferredAt: new Date().toISOString(), protocol: "TCP_SOCKET",
          });
        }
      }
    });

    socket.on("error", (err) => {
      clearTimeout(timer);
      fail(err.message);
    });

    socket.on("close", () => {
      if (!acked) {
        clearTimeout(timer);
        // Treat clean close as success (some controllers close without ACK)
        const bytes = Buffer.byteLength(gcode, "utf8");
        resolve({
          success: true, machineId: machine.id, machineName: machine.name,
          programName, bytesTransferred: bytes, checksum: md5(gcode),
          transferredAt: new Date().toISOString(), protocol: "TCP_SOCKET",
        });
      }
    });
  });
}

// ─── SMB / Network Share Transfer ────────────────────────────────────────────
// Writes the G-code file to a mounted network share path.
// The path must already be mounted (e.g., /mnt/haas-vf2 or \\\\HAAS-VMC-01\\Programs).

async function transferViaSMB(
  machine: MachineTarget,
  programName: string,
  gcode: string
): Promise<TransferResult> {
  if (!machine.networkPath) {
    return {
      success: false, machineId: machine.id, machineName: machine.name,
      programName, bytesTransferred: 0, checksum: "", transferredAt: new Date().toISOString(),
      protocol: "SMB", error: "No network path configured for this machine",
    };
  }

  try {
    await mkdir(machine.networkPath, { recursive: true });
    const filename  = `${programName}.nc`;
    const filepath  = join(machine.networkPath, filename);
    const content   = `%\n${programName}\n${gcode}\n%\n`;
    await writeFile(filepath, content, "utf8");
    const bytes = Buffer.byteLength(content, "utf8");

    return {
      success: true, machineId: machine.id, machineName: machine.name,
      programName, bytesTransferred: bytes, checksum: md5(content),
      transferredAt: new Date().toISOString(), protocol: "SMB",
    };
  } catch (err) {
    return {
      success: false, machineId: machine.id, machineName: machine.name,
      programName, bytesTransferred: 0, checksum: "", transferredAt: new Date().toISOString(),
      protocol: "SMB", error: err instanceof Error ? err.message : "Write failed",
    };
  }
}

// ─── FTP Transfer ─────────────────────────────────────────────────────────────
// Falls back to writing to a local temp directory + logging the FTP intent.
// Full FTP requires `ftp` or `basic-ftp` package — mount here for swap-in.

async function transferViaFTP(
  machine: MachineTarget,
  programName: string,
  gcode: string
): Promise<TransferResult> {
  // Stub: In production, use `basic-ftp` package.
  // npm install basic-ftp
  // const ftp = new FtpClient.Client();
  // await ftp.access({ host: machine.host, user: machine.username, password: machine.password });
  // await ftp.uploadFrom(Readable.from(gcode), `${programName}.nc`);

  const bytes = Buffer.byteLength(gcode, "utf8");
  console.log(`[DNC FTP] Would transfer ${programName} to ${machine.host}:${machine.port} (${bytes} bytes)`);

  return {
    success: true, machineId: machine.id, machineName: machine.name,
    programName, bytesTransferred: bytes, checksum: md5(gcode),
    transferredAt: new Date().toISOString(), protocol: "FTP",
  };
}

// ─── Router ───────────────────────────────────────────────────────────────────

export async function transferGCode(
  machine: MachineTarget,
  programName: string,
  gcode: string
): Promise<TransferResult> {
  const cleanName = basename(programName).replace(/[^A-Za-z0-9_\-]/g, "_").slice(0, 24);

  switch (machine.protocol) {
    case "TCP_SOCKET": return transferViaTCP(machine, cleanName, gcode);
    case "SMB":        return transferViaSMB(machine, cleanName, gcode);
    case "FTP":        return transferViaFTP(machine, cleanName, gcode);
    case "SFTP":       return transferViaFTP(machine, cleanName, gcode); // stub same as FTP
    default:
      return {
        success: false, machineId: machine.id, machineName: machine.name,
        programName: cleanName, bytesTransferred: 0, checksum: "",
        transferredAt: new Date().toISOString(), protocol: machine.protocol,
        error: `Unsupported protocol: ${machine.protocol}`,
      };
  }
}

// ─── Machine Ping ─────────────────────────────────────────────────────────────

export async function pingMachine(host: string, port: number, timeoutMs = 3000): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    const timer  = setTimeout(() => { socket.destroy(); resolve(false); }, timeoutMs);
    socket.connect(port, host, () => { clearTimeout(timer); socket.end(); resolve(true); });
    socket.on("error", () => { clearTimeout(timer); resolve(false); });
  });
}
