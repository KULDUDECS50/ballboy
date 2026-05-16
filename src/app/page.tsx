"use client";

import { useState } from "react";
import { parseYouTubeId } from "@/lib/youtube";
import type { Detection, FrameStats } from "@/lib/types";
import { VideoStage } from "@/components/video-stage";
import { StatPanel, Metric } from "@/components/stat-panel";

// Placeholder until the YOLO pipeline streams real data in.
const EMPTY_STATS: FrameStats = {
  frame: 0,
  fps: 0,
  detections: [],
  classCounts: {},
};

export default function Home() {
  const [url, setUrl] = useState("");
  const [videoId, setVideoId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Wired to placeholder data for now; this is what YOLO will feed.
  const [stats] = useState<FrameStats>(EMPTY_STATS);
  const detections: Detection[] = stats.detections;

  function loadVideo(e: React.FormEvent) {
    e.preventDefault();
    const id = parseYouTubeId(url);
    if (!id) {
      setError("That doesn't look like a valid YouTube link.");
      return;
    }
    setError(null);
    setVideoId(id);
  }

  return (
    <main className="flex min-h-full flex-col bg-neutral-950 text-neutral-100">
      <header className="border-b border-neutral-800 px-8 py-4">
        <div className="flex items-center gap-4">
          <h1 className="text-lg font-bold tracking-tight">
            ball<span className="text-emerald-400">boy</span>
          </h1>
          <form onSubmit={loadVideo} className="flex flex-1 gap-2">
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="Paste a YouTube link…"
              className="flex-1 rounded-md border border-neutral-800 bg-neutral-900 px-3 py-2 text-sm outline-none focus:border-emerald-500"
            />
            <button
              type="submit"
              className="rounded-md bg-emerald-500 px-4 py-2 text-sm font-semibold text-black hover:bg-emerald-400"
            >
              Analyze
            </button>
          </form>
        </div>
        {error && (
          <p className="mt-2 text-xs text-red-400">{error}</p>
        )}
      </header>

      <div className="grid flex-1 grid-rows-[minmax(0,1fr)_auto] gap-5 p-8">
        {/* Top row: player data · video · simple */}
        <div className="grid gap-5 lg:grid-cols-[340px_minmax(0,1fr)_300px]">
          {/* Left: player data — to be integrated */}
          <StatPanel title="Player Data">
            <div className="flex h-full min-h-[200px] items-center justify-center text-center text-neutral-600">
              To be integrated
            </div>
          </StatPanel>

          {/* Center: the video */}
          <div className="flex items-start justify-center">
            <VideoStage videoId={videoId} detections={detections} />
          </div>

          {/* Right: simple — for now */}
          <div className="flex flex-col gap-5">
            <StatPanel title="Detections">
              <Metric label="Objects" value={String(detections.length)} />
              <Metric label="FPS" value={stats.fps.toFixed(1)} />
            </StatPanel>
            <StatPanel title="Pipeline">
              <Metric label="Model" value="YOLO" />
              <Metric label="Status" value={videoId ? "Ready" : "Idle"} />
            </StatPanel>
          </div>
        </div>

        {/* Bottom: graphs — to be integrated */}
        <StatPanel title="Graphs">
          <div className="flex h-48 items-center justify-center text-center text-neutral-600">
            To be integrated
          </div>
        </StatPanel>
      </div>
    </main>
  );
}
