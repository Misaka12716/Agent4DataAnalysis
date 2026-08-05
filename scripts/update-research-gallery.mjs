import fs from "node:fs";
import path from "node:path";

const researchRoot = process.argv[2];
const outputRoot = process.argv[3];

if (!researchRoot || !outputRoot) {
  throw new Error("Usage: node update-research-gallery.mjs <research-root> <output-root>");
}

const group = "expanded-20260805";
const groupLabel = "2026-08-05 新增深度页";
const screenshotsDir = path.join(researchRoot, "screenshots/external", group);
const reportDir = path.join(researchRoot, "report");
const galleryPath = path.join(reportDir, "SCREENSHOT_GALLERY.html");
const indexPath = path.join(reportDir, "SCREENSHOT_INDEX.md");
const outputReportDir = path.join(outputRoot, "report");

fs.mkdirSync(outputReportDir, { recursive: true });

const imageNames = fs
  .readdirSync(screenshotsDir)
  .filter((name) => /\.(png|jpe?g)$/i.test(name))
  .sort((left, right) => left.localeCompare(right, "zh-CN"));

function readMetadata(imageName) {
  const metadataPath = path.join(screenshotsDir, imageName.replace(/\.(png|jpe?g)$/i, ".json"));
  if (!fs.existsSync(metadataPath)) return {};
  return JSON.parse(fs.readFileSync(metadataPath, "utf8"));
}

function detailsFor(imageName) {
  const metadata = readMetadata(imageName);
  const fallbacks = {
    "findin-after-175477.png": {
      title: "Findin 登录后首页",
      platform: "Findin 飞引",
      pageType: "登录后工作台",
    },
    "findin-auth-library.png": {
      title: "我的文献 - Findin 飞引",
      platform: "Findin 飞引",
      pageType: "登录后文献库",
    },
  };
  const fallback = fallbacks[imageName] ?? {};
  return {
    title: metadata.title || fallback.title || imageName.replace(/\.(png|jpe?g)$/i, ""),
    platform: metadata.platform || fallback.platform || inferPlatform(imageName),
    pageType: metadata.page_type || fallback.pageType || "深度功能页",
  };
}

function inferPlatform(imageName) {
  if (imageName.startsWith("findin")) return "Findin 飞引";
  if (imageName.startsWith("scienceone")) return "ScienceOne";
  if (imageName.startsWith("panshi")) return "磐石大模型";
  if (imageName.startsWith("lkstudio")) return "LKStudio";
  return "外部平台";
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

const entries = imageNames.map((imageName) => {
  const details = detailsFor(imageName);
  const relativePath = `external/${group}/${imageName}`;
  return { imageName, relativePath, ...details };
});

const imageTotal = countImages(path.join(researchRoot, "screenshots"));

function countImages(directory) {
  let total = 0;
  for (const item of fs.readdirSync(directory, { withFileTypes: true })) {
    const itemPath = path.join(directory, item.name);
    if (item.isDirectory()) total += countImages(itemPath);
    if (item.isFile() && /\.(png|jpe?g)$/i.test(item.name)) total += 1;
  }
  return total;
}

const galleryCards = entries
  .map(({ title, platform, pageType, relativePath }) => {
    const search = escapeHtml(`${title} ${platform} ${pageType} ${relativePath}`.toLowerCase());
    const safeTitle = escapeHtml(title);
    const safeLabel = escapeHtml(`${platform} · ${pageType}`);
    const safePath = escapeHtml(relativePath);
    return `<article class="shot" data-group="${group}" data-search="${search}"><a href="../screenshots/${safePath}" target="_blank"><img loading="lazy" src="../screenshots/${safePath}" alt="${safeTitle}"></a><div class="shot-copy"><strong>${safeTitle}</strong><span>${safeLabel}</span><code>${safePath}</code></div></article>`;
  })
  .join("");

const galleryStart = `<!-- ${group.toUpperCase()}-GALLERY-START -->`;
const galleryEnd = `<!-- ${group.toUpperCase()}-GALLERY-END -->`;
let gallery = fs.readFileSync(galleryPath, "utf8");
gallery = gallery.replace(new RegExp(`${galleryStart}[\\s\\S]*?${galleryEnd}`), "");
gallery = gallery.replace(/共 \d+ 张。/, `共 ${imageTotal} 张。`);
gallery = gallery.replace(/data-filter="all">全部 <b>\d+<\/b>/, `data-filter="all">全部 <b>${imageTotal}</b>`);
gallery = gallery.replace(
  new RegExp(`<button data-filter="${group}">[\\s\\S]*?<\\/button>`),
  "",
);
gallery = gallery.replace(
  '<button class="active" data-filter="all">',
  `<button class="active" data-filter="all">`,
);
gallery = gallery.replace(
  /(<button class="active" data-filter="all">[\s\S]*?<\/button>)/,
  `$1<button data-filter="${group}">${groupLabel} <b>${entries.length}</b></button>`,
);
gallery = gallery.replace(
  '<main><section class="grid" id="grid">',
  `<main><section class="grid" id="grid">${galleryStart}${galleryCards}${galleryEnd}`,
);

const markdownStart = `<!-- ${group.toUpperCase()}-INDEX-START -->`;
const markdownEnd = `<!-- ${group.toUpperCase()}-INDEX-END -->`;
const markdownEntries = entries
  .map(({ title, platform, pageType, relativePath }) => `- [${platform} · ${pageType} · ${title}](../screenshots/${relativePath})`)
  .join("\n");
const markdownSection = `${markdownStart}\n## ${groupLabel}（${entries.length}）\n\n${markdownEntries}\n${markdownEnd}\n\n`;
let index = fs.readFileSync(indexPath, "utf8");
index = index.replace(new RegExp(`${markdownStart}[\\s\\S]*?${markdownEnd}\\n*`), "");
index = index.replace(/共归档 \*\*\d+ 张\*\*/, `共归档 **${imageTotal} 张**`);
index = index.replace(/(## 当前 PickU：)/, `${markdownSection}$1`);

fs.writeFileSync(path.join(outputReportDir, "SCREENSHOT_GALLERY.html"), gallery);
fs.writeFileSync(path.join(outputReportDir, "SCREENSHOT_INDEX.md"), index);

console.log(JSON.stringify({ imageTotal, added: entries.length, outputReportDir }, null, 2));
