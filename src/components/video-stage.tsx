import type { Detection } from "@/lib/types";
import { DetectionOverlay } from "./detection-overlay";

/** The centered video: the YouTube embed with the detection overlay on top.
 *  Keeps a 16:9 box so the overlay coords line up with the frame. */
export function VideoStage({
  videoId,
  detections,
}: {
  videoId: string | null;
  detections: Detection[];
}) {
  return (
    <div className="relative aspect-video w-full overflow-hidden rounded-xl border border-neutral-800 bg-black shadow-2xl">
      {videoId ? (
        <>
          <iframe
            className="absolute inset-0 h-full w-full"
            src={`https://www.youtube.com/embed/${videoId}`}
            title="YouTube video player"
            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
            allowFullScreen
          />
          <DetectionOverlay detections={detections} />
        </>
      ) : (
        <div className="absolute inset-0 flex items-center justify-center text-neutral-600">
          Paste a YouTube link to begin
        </div>
      )}
    </div>
  );
}
