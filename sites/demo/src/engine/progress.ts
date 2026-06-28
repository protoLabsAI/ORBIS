/**
 * Download-progress aggregator.
 *
 * Transformers.js fires a progress event per model FILE, and several files
 * download concurrently — so naively showing the latest event makes the
 * label bounce between file names and the percent jump around. Instead we
 * sum bytes across all files into one monotonic percent and never surface a
 * file name.
 */
export type FileProgress = Map<string, { loaded: number; total: number }>;

interface RawProgress {
  status?: string;
  file?: string;
  loaded?: number;
  total?: number;
}

/** Fold one raw progress event into the running totals; return overall %
 *  (0–100), or null until any file has reported a size. */
export function trackProgress(files: FileProgress, p: RawProgress): number | null {
  if (p?.file) {
    if (p.status === 'done') {
      const f = files.get(p.file);
      if (f) f.loaded = f.total;
    } else {
      files.set(p.file, { loaded: p.loaded ?? 0, total: p.total ?? 0 });
    }
  }
  let loaded = 0;
  let total = 0;
  for (const f of files.values()) {
    loaded += f.loaded;
    total += f.total;
  }
  return total > 0 ? Math.min(100, Math.round((loaded / total) * 100)) : null;
}
