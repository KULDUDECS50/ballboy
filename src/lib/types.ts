/** A single YOLO detection. Coordinates are normalized 0..1 relative to the
 *  video frame so the overlay scales with the player. */
export interface Detection {
  id: string;
  label: string;
  confidence: number; // 0..1
  box: { x: number; y: number; w: number; h: number }; // normalized
}

/** Per-frame analytics summary that the panels around the video render. */
export interface FrameStats {
  frame: number;
  fps: number;
  detections: Detection[];
  classCounts: Record<string, number>;
}
