/**
 * Workbook, document, and slides built only from an answer the user already
 * holds. No total is added. Numbers are copied from the rows and the text.
 */

export type ExportAnswer = {
  answer_id?: string;
  text?: string;
  rows?: Record<string, unknown>[] | null;
};

function xml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function crc32(data: Uint8Array): number {
  let c = ~0;
  for (let i = 0; i < data.length; i += 1) {
    c ^= data[i];
    for (let k = 0; k < 8; k += 1) c = c & 1 ? (c >>> 1) ^ 0xedb88320 : c >>> 1;
  }
  return ~c >>> 0;
}

function concat(parts: Uint8Array[]): Uint8Array {
  const total = parts.reduce((n, part) => n + part.length, 0);
  const out = new Uint8Array(total);
  let offset = 0;
  for (const part of parts) {
    out.set(part, offset);
    offset += part.length;
  }
  return out;
}

/** ZIP store (no compression) so the XML stays readable and needs no library. */
export function zipStore(files: { name: string; data: Uint8Array }[]): Uint8Array {
  const locals: Uint8Array[] = [];
  const central: Uint8Array[] = [];
  let offset = 0;
  const encoder = new TextEncoder();
  for (const file of files) {
    const name = encoder.encode(file.name);
    const crc = crc32(file.data);
    const local = new Uint8Array(30 + name.length);
    const view = new DataView(local.buffer);
    view.setUint32(0, 0x04034b50, true);
    view.setUint16(4, 20, true);
    view.setUint32(14, crc, true);
    view.setUint32(18, file.data.length, true);
    view.setUint32(22, file.data.length, true);
    view.setUint16(26, name.length, true);
    local.set(name, 30);
    locals.push(local, file.data);
    const cen = new Uint8Array(46 + name.length);
    const cenView = new DataView(cen.buffer);
    cenView.setUint32(0, 0x02014b50, true);
    cenView.setUint16(4, 20, true);
    cenView.setUint16(6, 20, true);
    cenView.setUint32(16, crc, true);
    cenView.setUint32(20, file.data.length, true);
    cenView.setUint32(24, file.data.length, true);
    cenView.setUint16(28, name.length, true);
    cenView.setUint32(42, offset, true);
    cen.set(name, 46);
    central.push(cen);
    offset += local.length + file.data.length;
  }
  const centralBytes = concat(central);
  const end = new Uint8Array(22);
  const endView = new DataView(end.buffer);
  endView.setUint32(0, 0x06054b50, true);
  endView.setUint16(8, files.length, true);
  endView.setUint16(10, files.length, true);
  endView.setUint32(12, centralBytes.length, true);
  endView.setUint32(16, offset, true);
  return concat([...locals, centralBytes, end]);
}

function textFile(name: string, body: string): { name: string; data: Uint8Array } {
  return { name, data: new TextEncoder().encode(body) };
}

function blob(bytes: Uint8Array, type: string): Blob {
  return new Blob([bytes], { type });
}

export function triggerDownload(file: Blob, filename: string): void {
  const url = URL.createObjectURL(file);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function fileStem(answer: ExportAnswer, ext: string): string {
  const id = (answer.answer_id || "answer").replace(/[^A-Za-z0-9_-]/g, "").slice(0, 40);
  return `${id || "answer"}.${ext}`;
}

export function answerRows(answer: ExportAnswer): Record<string, unknown>[] {
  return Array.isArray(answer.rows) ? answer.rows : [];
}

function columns(rows: Record<string, unknown>[]): string[] {
  const keys: string[] = [];
  for (const row of rows) {
    for (const key of Object.keys(row)) {
      if (!keys.includes(key)) keys.push(key);
    }
  }
  return keys;
}

function colName(index: number): string {
  let n = index + 1;
  let name = "";
  while (n > 0) {
    const rem = (n - 1) % 26;
    name = String.fromCharCode(65 + rem) + name;
    n = Math.floor((n - 1) / 26);
  }
  return name;
}

function cellXml(ref: string, value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value)) {
    return `<c r="${ref}"><v>${value}</v></c>`;
  }
  const text = value == null ? "" : String(value);
  return `<c r="${ref}" t="inlineStr"><is><t>${xml(text)}</t></is></c>`;
}

export function xlsxBytes(rows: Record<string, unknown>[]): Uint8Array {
  const keys = columns(rows);
  const header = keys
    .map((key, index) => cellXml(`${colName(index)}1`, key))
    .join("");
  const body = rows
    .map((row, rowIndex) => {
      const cells = keys
        .map((key, index) => cellXml(`${colName(index)}${rowIndex + 2}`, row[key]))
        .join("");
      return `<row r="${rowIndex + 2}">${cells}</row>`;
    })
    .join("");
  const sheet = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<sheetData><row r="1">${header}</row>${body}</sheetData>
</worksheet>`;
  return zipStore([
    textFile(
      "[Content_Types].xml",
      `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>`,
    ),
    textFile(
      "_rels/.rels",
      `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>`,
    ),
    textFile(
      "xl/workbook.xml",
      `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Answer" sheetId="1" r:id="rId1"/></sheets>
</workbook>`,
    ),
    textFile(
      "xl/_rels/workbook.xml.rels",
      `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>`,
    ),
    textFile("xl/worksheets/sheet1.xml", sheet),
  ]);
}

function paragraph(text: string): string {
  return `<w:p><w:r><w:t xml:space="preserve">${xml(text)}</w:t></w:r></w:p>`;
}

function docTable(rows: Record<string, unknown>[]): string {
  const keys = columns(rows);
  if (!keys.length) return "";
  const cell = (text: string, header = false) =>
    `<w:tc><w:p><w:r>${header ? "<w:rPr><w:b/>" : ""}<w:t xml:space="preserve">${xml(text)}</w:t></w:r></w:p></w:tc>`;
  const header = `<w:tr>${keys.map((key) => cell(key, true)).join("")}</w:tr>`;
  const body = rows
    .map((row) => `<w:tr>${keys.map((key) => cell(row[key] == null ? "" : String(row[key]))).join("")}</w:tr>`)
    .join("");
  return `<w:tbl>${header}${body}</w:tbl>`;
}

export function docxBytes(answer: ExportAnswer): Uint8Array {
  const text = (answer.text || "").trim() || "No answer text.";
  const document = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body>${paragraph(text)}${docTable(answerRows(answer))}<w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr></w:body>
</w:document>`;
  return zipStore([
    textFile(
      "[Content_Types].xml",
      `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>`,
    ),
    textFile(
      "_rels/.rels",
      `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>`,
    ),
    textFile("word/document.xml", document),
  ]);
}

function slideText(answer: ExportAnswer): string {
  const lines = [(answer.text || "").trim() || "No answer text."];
  const rows = answerRows(answer);
  const keys = columns(rows);
  if (keys.length) lines.push(keys.join(" | "));
  for (const row of rows.slice(0, 30)) {
    lines.push(keys.map((key) => (row[key] == null ? "" : String(row[key]))).join(" | "));
  }
  if (rows.length > 30) lines.push("Further rows stay on the answer.");
  return lines.join("\n");
}

export function pptxBytes(answer: ExportAnswer): Uint8Array {
  const body = xml(slideText(answer));
  const slide = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
<p:cSld><p:spTree>
<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>
<p:sp>
<p:nvSpPr><p:cNvPr id="2" name="Body"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>
<p:spPr><a:xfrm><a:off x="457200" y="457200"/><a:ext cx="10972800" cy="5943600"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>
<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>${body}</a:t></a:r></a:p></p:txBody>
</p:sp>
</p:spTree></p:cSld>
<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sld>`;
  return zipStore([
    textFile(
      "[Content_Types].xml",
      `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
<Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
</Types>`,
    ),
    textFile(
      "_rels/.rels",
      `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>
</Relationships>`,
    ),
    textFile(
      "ppt/presentation.xml",
      `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
<p:sldIdLst><p:sldId id="256" r:id="rId2"/></p:sldIdLst>
<p:sldSz cx="12192000" cy="6858000"/>
</p:presentation>`,
    ),
    textFile(
      "ppt/_rels/presentation.xml.rels",
      `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>
</Relationships>`,
    ),
    textFile("ppt/slides/slide1.xml", slide),
  ]);
}

const XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
const PPTX_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation";

export function downloadXlsx(rows: Record<string, unknown>[], filename: string): void {
  triggerDownload(blob(xlsxBytes(rows), XLSX_TYPE), filename);
}

export function downloadDocx(answer: ExportAnswer): void {
  triggerDownload(blob(docxBytes(answer), DOCX_TYPE), fileStem(answer, "docx"));
}

export function downloadPptx(answer: ExportAnswer): void {
  triggerDownload(blob(pptxBytes(answer), PPTX_TYPE), fileStem(answer, "pptx"));
}
