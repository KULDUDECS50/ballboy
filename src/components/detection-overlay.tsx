import type { Detection } from "@/lib/types";

/** Transparent layer that sits on top of the video and draws YOLO boxes.
 *  Boxes use normalized coords so it scales with whatever size the video is. */
export function DetectionOverlay({ detections }: { detections: Detection[] }) {
  return (
    <div className="pointer-events-none absolute inset-0">
      {detections.map((d) => (
        <div
          key={d.id}
          className="absolute rounded-sm border-2 border-emerald-400"
          style={{
            left: `${d.box.x * 100}%`,
            top: `${d.box.y * 100}%`,
            width: `${d.box.w * 100}%`,
            height: `${d.box.h * 100}%`,
          }}
        >
          <span className="absolute -top-6 left-0 whitespace-nowrap rounded bg-emerald-400 px-1.5 py-0.5 text-xs font-semibold text-black">
            {d.label} {(d.confidence * 100).toFixed(0)}%
          </span>
        </div>
      ))}
    </div>
  );
}
