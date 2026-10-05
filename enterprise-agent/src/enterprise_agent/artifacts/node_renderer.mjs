import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [entrypointPath, inputPath, outputPath, previewDir] = process.argv.slice(2);
if (!entrypointPath || !inputPath || !outputPath || !previewDir) {
  throw new Error("Usage: node_renderer.mjs <entrypoint> <input> <output> <preview-dir>");
}

const artifactTool = await import(pathToFileURL(path.resolve(entrypointPath)).href);
const payload = JSON.parse(await fs.readFile(inputPath, "utf8"));
await fs.mkdir(previewDir, { recursive: true });

if (payload.spec.artifact_type === "xlsx") {
  await renderWorkbook(payload, outputPath, previewDir, artifactTool);
} else if (payload.spec.artifact_type === "pptx") {
  await renderPresentation(payload, outputPath, previewDir, artifactTool);
} else {
  throw new Error(`Unsupported artifact type: ${payload.spec.artifact_type}`);
}

async function renderWorkbook(payload, finalPath, previews, tool) {
  const { SpreadsheetFile, Workbook } = tool;
  const { spec, template } = payload;
  const workbook = Workbook.create();
  const summary = workbook.worksheets.add("Summary");
  summary.showGridLines = false;

  summary.getRange("A1:H1").merge();
  summary.getRange("A1").values = [[spec.title]];
  summary.getRange("A1:H1").format = {
    fill: template.primary_color,
    font: { bold: true, color: "#FFFFFF", size: 20 },
    rowHeight: 34,
    verticalAlignment: "center",
  };
  summary.getRange("A2:H2").merge();
  summary.getRange("A2").values = [[spec.subtitle || `Prepared for ${template.company_name}`]];
  summary.getRange("A2:H2").format = {
    font: { color: "#475467", italic: true, size: 11 },
    rowHeight: 24,
  };

  let row = 4;
  for (const section of spec.sections) {
    summary.getRange(`A${row}:H${row}`).merge();
    summary.getRange(`A${row}`).values = [[section.heading]];
    summary.getRange(`A${row}:H${row}`).format = {
      fill: "#F2F4F7",
      font: { bold: true, color: template.primary_color, size: 12 },
      rowHeight: 24,
      verticalAlignment: "center",
    };
    row += 1;
    if (section.body) {
      for (const chunk of splitText(section.body, 500)) {
        summary.getRange(`A${row}:H${row}`).merge();
        summary.getRange(`A${row}`).values = [[chunk]];
        summary.getRange(`A${row}:H${row}`).format = {
          wrapText: true,
          rowHeight: Math.min(90, 26 + Math.ceil(chunk.length / 120) * 14),
          verticalAlignment: "top",
        };
        row += 1;
      }
    }
    for (const bullet of section.bullets) {
      for (const [chunkIndex, chunk] of splitText(bullet, 400).entries()) {
        summary.getRange(`A${row}`).values = [[chunkIndex === 0 ? "•" : ""]];
        summary.getRange(`B${row}:H${row}`).merge();
        summary.getRange(`B${row}`).values = [[chunk]];
        summary.getRange(`B${row}:H${row}`).format = { wrapText: true, verticalAlignment: "top" };
        row += 1;
      }
    }
    row += 1;
  }
  const summaryEndRow = Math.max(2, row);
  summary.getRange(`A1:H${summaryEndRow}`).format.columnWidth = 14;
  summary.getRange(`A1:A${summaryEndRow}`).format.columnWidth = 4;
  summary.freezePanes.freezeRows(2);

  for (const [index, tableSpec] of spec.tables.entries()) {
    const sheetName = uniqueSheetName(tableSpec.title || `Table ${index + 1}`, index);
    const sheet = workbook.worksheets.add(sheetName);
    sheet.showGridLines = false;
    const matrix = [tableSpec.columns, ...tableSpec.rows];
    const endColumn = columnName(tableSpec.columns.length);
    sheet.getRange(`A1:${endColumn}${matrix.length}`).values = matrix;
    sheet.getRange(`A1:${endColumn}1`).format = {
      fill: template.primary_color,
      font: { bold: true, color: "#FFFFFF" },
      wrapText: true,
      rowHeight: 28,
      verticalAlignment: "center",
    };
    if (matrix.length > 1) {
      sheet.getRange(`A2:${endColumn}${matrix.length}`).format = {
        borders: { preset: "inside", style: "thin", color: "#EAECF0" },
        verticalAlignment: "top",
      };
      for (const [columnIndex, column] of tableSpec.columns.entries()) {
        const normalized = column.toLowerCase();
        const values = normalized === "status"
          ? ["Not started", "In progress", "Open", "Blocked", "At risk", "Done"]
          : normalized === "priority"
            ? ["Low", "Medium", "High", "Critical"]
            : null;
        if (values) {
          const letter = columnName(columnIndex + 1);
          sheet.getRange(`${letter}2:${letter}${matrix.length}`).dataValidation = {
            rule: { type: "list", values },
          };
        }
      }
    }
    sheet.getRange(`A1:${endColumn}${matrix.length}`).format.autofitColumns();
    for (let column = 0; column < tableSpec.columns.length; column += 1) {
      const letter = columnName(column + 1);
      sheet.getRange(`${letter}1:${letter}${matrix.length}`).format.columnWidth = Math.min(
        36,
        Math.max(
          12,
          sheet.getRange(`${letter}1:${letter}${matrix.length}`).format.columnWidth || 12,
        ),
      );
    }
    sheet.freezePanes.freezeRows(1);
  }

  const inspection = await workbook.inspect({
    kind: "workbook,sheet,formula",
    maxChars: 12000,
    tableMaxRows: 20,
    tableMaxCols: 12,
  });
  const inspectionText = inspection.ndjson || JSON.stringify(inspection);
  if (/#REF!|#DIV\/0!|#VALUE!|#NAME\?|#N\/A/.test(inspectionText)) {
    throw new Error("Workbook validation found a formula error");
  }
  for (const [index, sheet] of workbook.worksheets.items.entries()) {
    const preview = await workbook.render({
      sheetName: sheet.name,
      autoCrop: "all",
      scale: 1,
      format: "png",
    });
    await writeBlob(
      path.join(previews, `sheet-${String(index + 1).padStart(2, "0")}-${safeStem(sheet.name)}.png`),
      preview,
    );
  }
  const xlsx = await SpreadsheetFile.exportXlsx(workbook);
  await xlsx.save(finalPath);
}

async function renderPresentation(payload, finalPath, previews, tool) {
  const { Presentation, PresentationFile, layers, table, text } = tool;
  const { spec, template } = payload;
  const presentation = Presentation.create({ slideSize: { width: 1280, height: 720 } });
  const sourceNotes = spec.sources.length
    ? `[Sources]\n${spec.sources.map((source) => `${source.label}: ${source.reference}`).join("\n")}`
    : "[Sources]\nNo external sources supplied.";

  const cover = presentation.slides.add();
  cover.compose(
    layers({ name: "codex-grid-layout-library#slide-01", width: "fill", height: "fill" }, [
      text([template.company_name.toUpperCase()], {
        name: "company",
        position: { left: 41.33, top: 41.18 },
        width: 598.67,
        height: 68.15,
        style: textStyle(24, template.primary_color),
      }),
      text([spec.title], {
        name: "title",
        position: { left: 41.33, top: 182.55 },
        width: 992,
        height: 261.57,
        style: { ...textStyle(64, "#000000"), verticalAlignment: "bottom", autoFit: "shrinkText" },
      }),
      text([spec.subtitle || "Enterprise artifact"], {
        name: "subtitle",
        position: { left: 41.33, top: 497.87 },
        width: 760,
        height: 113.41,
        style: { ...textStyle(28, "#475467"), autoFit: "shrinkText" },
      }),
    ]),
    { frame: { left: 0, top: 0, width: 1280, height: 720 }, baseUnit: 1 },
  );
  cover.speakerNotes.textFrame.setText(sourceNotes);

  for (const [index, section] of spec.sections.entries()) {
    const slide = presentation.slides.add();
    const bodyParts = [section.body, ...section.bullets.map((bullet) => `• ${bullet}`)].filter(Boolean);
    const midpoint = Math.max(1, Math.ceil(bodyParts.length / 2));
    const leftText = bodyParts.slice(0, midpoint).join("\n\n");
    const rightText = bodyParts.slice(midpoint).join("\n\n") || "Key evidence and follow-up items are captured in the source material.";
    slide.compose(
      layers({ name: "codex-grid-layout-library#slide-04", width: "fill", height: "fill" }, [
        text([section.heading], {
          name: "title",
          position: { left: 41.33, top: 36.12 },
          width: 1197.33,
          height: 109.97,
          style: { ...textStyle(38.67, "#000000"), autoFit: "shrinkText" },
        }),
        text([leftText], {
          name: "left-content",
          position: { left: 41.33, top: 193.05 },
          width: 580.91,
          height: 436.28,
          style: { ...textStyle(24, "#101828"), autoFit: "shrinkText", wrap: "square" },
        }),
        text([rightText], {
          name: "right-content",
          position: { left: 657.75, top: 193.05 },
          width: 581.33,
          height: 436.28,
          style: { ...textStyle(24, "#101828"), autoFit: "shrinkText", wrap: "square" },
        }),
        text([String(index + 2)], {
          name: "page-number",
          position: { left: 1184.18, top: 659.24 },
          width: 54.48,
          height: 25.33,
          style: { ...textStyle(13.33, "#667085"), alignment: "right" },
        }),
      ]),
      { frame: { left: 0, top: 0, width: 1280, height: 720 }, baseUnit: 1 },
    );
    slide.speakerNotes.textFrame.setText(sourceNotes);
  }

  for (const tableSpec of spec.tables) {
    for (let offset = 0; offset < Math.max(1, tableSpec.rows.length); offset += 8) {
      const rows = tableSpec.rows.slice(offset, offset + 8);
      const values = [tableSpec.columns, ...rows];
      const slide = presentation.slides.add();
      slide.compose(
        layers({ name: "codex-grid-layout-library#slide-14", width: "fill", height: "fill" }, [
          text([tableSpec.title || "Evidence table"], {
            name: "title",
            position: { left: 41.33, top: 36.12 },
            width: 1197.33,
            height: 109.97,
            style: { ...textStyle(38.67, "#000000"), autoFit: "shrinkText" },
          }),
          table({
            name: "evidence-table",
            rows: values.length,
            columns: tableSpec.columns.length,
            values,
            columnWidths: Array(tableSpec.columns.length).fill(1197.33 / tableSpec.columns.length),
            position: { left: 41.33, top: 190 },
            width: 1197.33,
            height: Math.min(450, Math.max(100, values.length * 48)),
          }),
          text([String(presentation.slides.items.length)], {
            name: "page-number",
            position: { left: 1184.18, top: 659.24 },
            width: 54.48,
            height: 25.33,
            style: { ...textStyle(13.33, "#667085"), alignment: "right" },
          }),
        ]),
        { frame: { left: 0, top: 0, width: 1280, height: 720 }, baseUnit: 1 },
      );
      slide.speakerNotes.textFrame.setText(sourceNotes);
      if (!tableSpec.rows.length) break;
    }
  }

  for (const [index, slide] of presentation.slides.items.entries()) {
    const rendered = await presentation.export({ slide, format: "png", scale: 1 });
    await writeBlob(path.join(previews, `slide-${String(index + 1).padStart(2, "0")}.png`), rendered);
    const layout = await slide.export({ format: "layout" });
    const layoutText = await layout.text();
    await fs.writeFile(path.join(previews, `slide-${String(index + 1).padStart(2, "0")}.layout.json`), layoutText);
    const parsed = JSON.parse(layoutText);
    if (JSON.stringify(parsed).includes('"overflow":true')) {
      throw new Error(`Slide ${index + 1} contains overflowing content`);
    }
  }
  const pptx = await PresentationFile.exportPptx(presentation);
  await pptx.save(finalPath);
}

function textStyle(fontSize, color) {
  return {
    fontSize: `${fontSize}px`,
    typeface: "Helvetica Neue",
    color,
    alignment: "left",
    verticalAlignment: "top",
    insets: { top: 0, right: 0, bottom: 0, left: 0 },
  };
}

function uniqueSheetName(value, index) {
  const cleaned = String(value).replace(/[\\/?*\[\]:]/g, " ").trim().slice(0, 25) || "Table";
  return `${cleaned} ${index + 1}`.slice(0, 31);
}

function safeStem(value) {
  return String(value).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "sheet";
}

function splitText(value, maxLength) {
  const words = String(value).trim().split(/\s+/);
  const chunks = [];
  let current = "";
  for (const word of words) {
    if (current && current.length + word.length + 1 > maxLength) {
      chunks.push(current);
      current = word;
    } else {
      current = current ? `${current} ${word}` : word;
    }
  }
  if (current) chunks.push(current);
  return chunks.length ? chunks : [""];
}

function columnName(count) {
  let result = "";
  let value = count;
  while (value > 0) {
    value -= 1;
    result = String.fromCharCode(65 + (value % 26)) + result;
    value = Math.floor(value / 26);
  }
  return result;
}

async function writeBlob(filePath, blob) {
  await fs.writeFile(filePath, new Uint8Array(await blob.arrayBuffer()));
}
