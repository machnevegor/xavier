import { describe, expect, it } from "vitest";
import type { Media } from "@/api/types.ts";
import { galleryTiles } from "@/features/chats/media.ts";

function item(patch: Partial<Media> & Pick<Media, "id">): Media {
  return {
    artifact_id: null,
    content_type: "image/png",
    created_at: "2026-01-01T00:00:00Z",
    event_id: null,
    kind: "artifact",
    name: patch.id,
    size_bytes: 10,
    source_url: null,
    url: `/artifacts/${patch.id}/content`,
    ...patch,
  };
}

describe("galleryTiles", () => {
  it("drops a repeated id, artifact and source URL, and keeps the first", () => {
    const tiles = galleryTiles([
      item({ artifact_id: "a1", id: "a1", name: "shot.png" }),
      item({ artifact_id: "a1", id: "again", name: "shot-copy.png" }),
      item({
        artifact_id: null,
        event_id: "e1",
        id: "e1:0",
        kind: "external",
        name: "photo.jpg",
        source_url: "https://cdn.example/photo.jpg",
        url: "/chats/c/events/e1/media/0",
      }),
      item({
        artifact_id: null,
        event_id: "e2",
        id: "e2:0",
        kind: "external",
        name: "photo-again.jpg",
        source_url: "https://cdn.example/photo.jpg",
        url: "/chats/c/events/e2/media/0",
      }),
      item({ id: "same" }),
      item({ id: "same", name: "duplicate" }),
    ]);

    expect(tiles.map((tile) => tile.name)).toEqual([
      "shot.png",
      "photo.jpg",
      "same",
    ]);
    expect(tiles[0]?.src).toBe("/api/v1/artifacts/a1/content");
    expect(tiles[1]?.download).toBe("/api/v1/chats/c/events/e1/media/0");
  });
});
