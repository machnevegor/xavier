import type { Media } from "@/api/types.ts";

export interface GalleryTile {
  id: string;
  name: string;
  src: string;
  download: string;
}

/** API paths are relative to `/api/v1`. */
export function mediaSrc(path: string): string {
  if (path.startsWith("/api/")) {
    return path;
  }
  return path.startsWith("/") ? `/api/v1${path}` : `/api/v1/${path}`;
}

/**
 * Tiles for the gallery, in the order the API returned them.
 *
 * The media endpoint already drops duplicate image bytes and duplicate web
 * addresses. A repeated id, artifact or source URL still collapses to the first
 * tile, so the grid never shows the same picture twice.
 */
export function galleryTiles(items: readonly Media[]): GalleryTile[] {
  const seen = new Set<string>();
  const tiles: GalleryTile[] = [];
  for (const item of items) {
    const keys = [
      `id:${item.id}`,
      item.artifact_id ? `artifact:${item.artifact_id}` : null,
      item.source_url ? `url:${item.source_url}` : null,
    ].filter((key): key is string => key !== null);
    if (keys.some((key) => seen.has(key))) {
      continue;
    }
    for (const key of keys) {
      seen.add(key);
    }
    const src = mediaSrc(item.url);
    tiles.push({ download: src, id: item.id, name: item.name, src });
  }
  return tiles;
}
