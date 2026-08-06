import { describe, expect, it } from "vitest";
import { isFilesystemOriginUri } from "./filesystemOrigin";

describe("isFilesystemOriginUri", () => {
  it("accepts Windows and UNC absolute paths", () => {
    expect(isFilesystemOriginUri(String.raw`D:\DMS\data\notes.xlsx`)).toBe(true);
    expect(isFilesystemOriginUri("D:/DMS/data/notes.xlsx")).toBe(true);
    expect(isFilesystemOriginUri(String.raw`\\server\share\file.csv`)).toBe(true);
  });

  it("rejects non-filesystem and relative uris", () => {
    expect(isFilesystemOriginUri("duckdb://dms_demo/transactions")).toBe(false);
    expect(isFilesystemOriginUri("https://example.com/a.xlsx")).toBe(false);
    expect(isFilesystemOriginUri("notes.xlsx")).toBe(false);
    expect(isFilesystemOriginUri("")).toBe(false);
    expect(isFilesystemOriginUri(null)).toBe(false);
  });
});
