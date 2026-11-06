"use client";

// Three.js 3D toolpath canvas
// Renders: stock bounding box, colored toolpath lines (rapid=green, feed=cyan, arc=red),
// animated cutter cylinder, axes helpers, orbit controls.

import { useEffect, useRef, useCallback } from "react";
import type { ParseResult } from "@/lib/gcode/parser";

// Color map per move type
const MOVE_COLORS: Record<string, number> = {
  rapid:   0x00ff88,   // bright green
  feed:    0x00e5ff,   // cyan
  arc_cw:  0xff4444,   // red
  arc_ccw: 0xff8800,   // orange
};

interface Props {
  parseResult: ParseResult | null;
  highlightLine?: number;   // G-code line to highlight (from editor click)
  isPlaying?: boolean;      // animate cutter along path
  onSegmentClick?: (lineIndex: number) => void;
}

export default function Canvas3D({ parseResult, highlightLine, isPlaying, onSegmentClick }: Props) {
  const mountRef   = useRef<HTMLDivElement>(null);
  const sceneRef   = useRef<unknown>(null);   // THREE.Scene
  const rendererRef = useRef<unknown>(null);  // THREE.WebGLRenderer
  const cameraRef  = useRef<unknown>(null);   // THREE.PerspectiveCamera
  const controlsRef = useRef<unknown>(null);
  const rafRef     = useRef<number>(0);
  const cutterRef  = useRef<unknown>(null);   // THREE.Mesh cutter
  const playProgressRef = useRef<number>(0);

  // ── Init scene ──────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!mountRef.current) return;
    const container = mountRef.current;

    let scene: import("three").Scene;
    let renderer: import("three").WebGLRenderer;
    let camera: import("three").PerspectiveCamera;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let controls: any;

    (async () => {
      try {
      const THREE = await import("three");
      const { OrbitControls } = await import("three/examples/jsm/controls/OrbitControls.js");

      // Scene
      scene = new THREE.Scene();
      scene.background = new THREE.Color(0x0f172a);  // slate-900
      sceneRef.current = scene;

      // Renderer
      renderer = new THREE.WebGLRenderer({ antialias: true });
      renderer.setPixelRatio(window.devicePixelRatio);
      renderer.setSize(container.clientWidth, container.clientHeight);
      container.appendChild(renderer.domElement);
      rendererRef.current = renderer;

      // Camera
      camera = new THREE.PerspectiveCamera(50, container.clientWidth / container.clientHeight, 0.1, 10000);
      camera.position.set(150, 150, 200);
      cameraRef.current = camera;

      // Orbit Controls
      controls = new OrbitControls(camera, renderer.domElement);
      controls.enableDamping = true;
      controls.dampingFactor = 0.05;
      controlsRef.current = controls;

      // Grid
      const grid = new THREE.GridHelper(300, 30, 0x1e293b, 0x1e293b);
      scene.add(grid);

      // Axes
      const axes = new THREE.AxesHelper(30);
      scene.add(axes);

      // Ambient + directional light
      scene.add(new THREE.AmbientLight(0xffffff, 0.4));
      const dir = new THREE.DirectionalLight(0xffffff, 0.8);
      dir.position.set(100, 200, 100);
      scene.add(dir);

      // Cutter tool (cylinder placeholder)
      const cutterGeo = new THREE.CylinderGeometry(3, 3, 20, 16);
      const cutterMat = new THREE.MeshStandardMaterial({ color: 0xe2e8f0 });
      const cutter = new THREE.Mesh(cutterGeo, cutterMat);
      cutter.position.set(0, 10, 0);
      scene.add(cutter);
      cutterRef.current = cutter;

      // Resize observer
      const ro = new ResizeObserver(() => {
        if (!container) return;
        camera.aspect = container.clientWidth / container.clientHeight;
        camera.updateProjectionMatrix();
        renderer.setSize(container.clientWidth, container.clientHeight);
      });
      ro.observe(container);

      // Render loop
      const animate = () => {
        rafRef.current = requestAnimationFrame(animate);
        controls.update();
        renderer.render(scene, camera);
      };
      animate();
      } catch (err) {
        console.error("[Canvas3D] Init error:", err);
      }
    })();

    return () => {
      cancelAnimationFrame(rafRef.current);
      if (rendererRef.current) {
        const r = rendererRef.current as import("three").WebGLRenderer;
        r.dispose();
        if (container.contains(r.domElement)) container.removeChild(r.domElement);
      }
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Rebuild toolpath geometry when parseResult changes ────────────────────
  useEffect(() => {
    if (!sceneRef.current || !parseResult) return;

    (async () => {
      const THREE = await import("three");
      const scene = sceneRef.current as import("three").Scene;

      // Remove old toolpath group
      const old = scene.getObjectByName("toolpath");
      if (old) scene.remove(old);
      const oldStock = scene.getObjectByName("stock");
      if (oldStock) scene.remove(oldStock);

      const group = new THREE.Group();
      group.name = "toolpath";

      // Draw each segment as a line
      for (const seg of parseResult.segments) {
        const color = MOVE_COLORS[seg.type] ?? 0xffffff;
        const mat   = new THREE.LineBasicMaterial({ color });
        const geo   = new THREE.BufferGeometry().setFromPoints([
          new THREE.Vector3(seg.from[0], seg.from[2], -seg.from[1]),  // Y-up: swap Y↔Z
          new THREE.Vector3(seg.to[0],   seg.to[2],   -seg.to[1]),
        ]);
        const line = new THREE.Line(geo, mat);
        line.userData.lineIndex = seg.lineIndex;
        group.add(line);
      }

      scene.add(group);

      // Stock bounding box wireframe
      const { min, max } = parseResult.bounds;
      const stockW = (max[0] - min[0]) + 6;
      const stockH = (max[2] - min[2]) + 6;
      const stockD = (max[1] - min[1]) + 6;
      const stockGeo = new THREE.BoxGeometry(stockW, stockH || 6, stockD);
      const stockMat = new THREE.MeshBasicMaterial({ color: 0x334155, wireframe: true });
      const stock    = new THREE.Mesh(stockGeo, stockMat);
      stock.name = "stock";
      stock.position.set(
        (min[0] + max[0]) / 2,
        (min[2] + max[2]) / 2 - 3,
        -(min[1] + max[1]) / 2
      );
      scene.add(stock);

      // Fit camera
      if (cameraRef.current) {
        const cam = cameraRef.current as import("three").PerspectiveCamera;
        const cx = (min[0] + max[0]) / 2;
        const cy = (min[2] + max[2]) / 2;
        const cz = -(min[1] + max[1]) / 2;
        const size = Math.max(max[0] - min[0], max[1] - min[1], max[2] - min[2]);
        cam.position.set(cx + size, cy + size * 0.8, cz + size);
        cam.lookAt(cx, cy, cz);
        if (controlsRef.current) {
          const ctrl = controlsRef.current as { target: import("three").Vector3 };
          ctrl.target.set(cx, cy, cz);
        }
      }
    })();
  }, [parseResult]);

  // ── Highlight line ────────────────────────────────────────────────────────
  useEffect(() => {
    if (!sceneRef.current || !parseResult || highlightLine === undefined) return;

    (async () => {
      const THREE = await import("three");
      const scene = sceneRef.current as import("three").Scene;

      // Reset all line colors
      const group = scene.getObjectByName("toolpath") as import("three").Group | undefined;
      if (!group) return;

      group.traverse((obj) => {
        if (obj instanceof THREE.Line) {
          const mat = obj.material as import("three").LineBasicMaterial;
          mat.color.setHex(MOVE_COLORS[parseResult.segments[0]?.type ?? "feed"] ?? 0xffffff);
        }
      });

      // Find segments for this line and highlight them yellow
      group.traverse((obj) => {
        if (obj instanceof THREE.Line && obj.userData.lineIndex === highlightLine) {
          const mat = obj.material as import("three").LineBasicMaterial;
          mat.color.setHex(0xfbbf24);   // amber highlight
        }
      });

      // Move cutter to that position
      const segIdx = parseResult.lineToSegment.get(highlightLine);
      if (segIdx !== undefined && cutterRef.current) {
        const seg = parseResult.segments[segIdx];
        const cutter = cutterRef.current as import("three").Mesh;
        cutter.position.set(seg.to[0], seg.to[2] + 10, -seg.to[1]);
      }
    })();
  }, [highlightLine, parseResult]);

  // ── Animation playback ────────────────────────────────────────────────────
  useEffect(() => {
    if (!isPlaying || !parseResult) return;

    const segs = parseResult.segments;
    if (!segs.length) return;

    let idx = 0;
    const interval = setInterval(async () => {
      if (idx >= segs.length) {
        clearInterval(interval);
        return;
      }
      const THREE = await import("three");
      const seg = segs[idx++];
      const cutter = cutterRef.current as import("three").Mesh | null;
      if (cutter) cutter.position.set(seg.to[0], seg.to[2] + 10, -seg.to[1]);
      onSegmentClick?.(seg.lineIndex);
    }, 30);

    return () => clearInterval(interval);
  }, [isPlaying, parseResult, onSegmentClick]);

  // ── Click-to-select ───────────────────────────────────────────────────────
  const handleClick = useCallback(async (e: React.MouseEvent<HTMLDivElement>) => {
    if (!sceneRef.current || !rendererRef.current || !cameraRef.current || !parseResult) return;
    if (!mountRef.current) return;

    // Use mountRef directly — e.currentTarget is unreliable in async handlers in React 19
    const rect    = mountRef.current.getBoundingClientRect();
    const clientX = e.clientX;
    const clientY = e.clientY;

    const THREE = await import("three");
    const ndc  = new THREE.Vector2(
      ((clientX - rect.left) / rect.width)  * 2 - 1,
      -((clientY - rect.top) / rect.height) * 2 + 1,
    );

    const raycaster = new THREE.Raycaster();
    raycaster.setFromCamera(ndc, cameraRef.current as import("three").PerspectiveCamera);

    const scene   = sceneRef.current as import("three").Scene;
    const group   = scene.getObjectByName("toolpath") as import("three").Group | undefined;
    if (!group) return;

    const hits = raycaster.intersectObjects(group.children, false);
    if (hits.length > 0) {
      const lineIdx = hits[0].object.userData.lineIndex as number | undefined;
      if (lineIdx !== undefined) onSegmentClick?.(lineIdx);
    }
  }, [parseResult, onSegmentClick]);

  return (
    <div
      ref={mountRef}
      className="w-full h-full"
      onClick={handleClick}
      style={{ cursor: "crosshair" }}
    />
  );
}
