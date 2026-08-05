import fs from "node:fs";
import path from "node:path";

const researchRoot = process.argv[2];
const outputRoot = process.argv[3];

if (!researchRoot || !outputRoot) {
  throw new Error("Usage: node scripts/build-single-page-screenshot-set.mjs <research-root> <output-root>");
}

const galleryPath = path.join(researchRoot, "report", "SCREENSHOT_GALLERY.html");
const screenshotsRoot = path.join(researchRoot, "screenshots");
const singleRoot = path.join(outputRoot, "single-page-screenshots");
const reportRoot = path.join(outputRoot, "report");
const singleGalleryPath = path.join(reportRoot, "SINGLE_PAGE_GALLERY.html");
const singleIndexPath = path.join(reportRoot, "SINGLE_PAGE_INDEX.md");

const gallery = fs.readFileSync(galleryPath, "utf8");
const articleRe = /<article class="shot" data-group="([^"]+)"[^>]*>[\s\S]*?<a href="\.\.\/screenshots\/([^"]+)"[\s\S]*?<strong>([\s\S]*?)<\/strong><span>([\s\S]*?)<\/span>/g;

const excludedGroups = new Set(["current-overviews"]);
const usedNames = new Map();
const entries = [];

function decodeHtml(value) {
  return value
    .replaceAll("&amp;", "&")
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&quot;", '"')
    .replaceAll("&#39;", "'");
}

function normalizeName(value) {
  const normalized = decodeHtml(value)
    .replace(/\s*[·|｜]\s*/g, "-")
    .replace(/\s+/g, "-")
    .replace(/[\\/:*?"<>|()[\]{}]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .replace(/-+/g, "-")
    .toLowerCase();
  return normalized || "page";
}

function uniqueFileName(base, extension) {
  const previous = usedNames.get(base) || 0;
  usedNames.set(base, previous + 1);
  if (previous === 0) return `${base}${extension}`;
  return `${base}-${previous + 1}${extension}`;
}

fs.rmSync(singleRoot, { recursive: true, force: true });
fs.mkdirSync(singleRoot, { recursive: true });
fs.mkdirSync(reportRoot, { recursive: true });

let match;
while ((match = articleRe.exec(gallery))) {
  const [, group, relativePath, rawTitle, rawMeta] = match;
  if (excludedGroups.has(group)) continue;

  const sourcePath = path.join(screenshotsRoot, relativePath);
  if (!fs.existsSync(sourcePath)) continue;

  const title = decodeHtml(rawTitle.replace(/<[^>]+>/g, "")).trim();
  const meta = decodeHtml(rawMeta.replace(/<[^>]+>/g, "")).trim();
  const [platformPart = "", functionPart = ""] = meta.split("·").map((item) => item.trim());
  const extension = path.extname(sourcePath).toLowerCase() || ".png";
  const base = normalizeName(`${platformPart || "platform"}-${functionPart || title}-${title}`);
  const fileName = uniqueFileName(base, extension);
  const targetPath = path.join(singleRoot, fileName);

  fs.copyFileSync(sourcePath, targetPath);
  entries.push({
    group,
    source: path.relative(outputRoot, sourcePath),
    target: path.relative(outputRoot, targetPath),
    title,
    platform: platformPart || "未标注平台",
    functionName: functionPart || title,
  });
}

entries.sort((left, right) => left.target.localeCompare(right.target, "zh-CN"));

const cards = entries
  .map((entry) => {
    const src = `../${entry.target}`;
    const search = `${entry.platform} ${entry.functionName} ${entry.title} ${entry.target}`.toLowerCase();
    return `<article class="shot" data-search="${escapeHtml(search)}"><a href="${escapeHtml(src)}" target="_blank"><img loading="lazy" src="${escapeHtml(src)}" alt="${escapeHtml(entry.title)}"></a><div><strong>${escapeHtml(entry.title)}</strong><span>${escapeHtml(entry.platform)} · ${escapeHtml(entry.functionName)}</span><code>${escapeHtml(entry.target)}</code></div></article>`;
  })
  .join("\n");

const html = `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>单页功能截图图库</title>
<style>
body{margin:0;background:#eef2f6;color:#10243e;font-family:"Noto Sans CJK SC","Microsoft YaHei",sans-serif}
header{position:sticky;top:0;z-index:2;background:rgba(255,255,255,.96);border-bottom:1px solid #d7dee7;padding:22px 30px 16px}
h1{margin:0 0 8px;font-size:26px}p{margin:0;color:#647181}.search{margin-top:16px;width:min(520px,100%);border:1px solid #d7dee7;border-radius:8px;padding:10px 12px;font:inherit}
main{padding:26px 30px 56px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:18px;align-items:start}
.shot{background:#fff;border:1px solid #d7dee7;border-radius:10px;overflow:hidden;box-shadow:0 5px 18px rgba(16,36,62,.06)}.shot[hidden]{display:none}
.shot a{display:block;height:238px;background:#e4eaf0;overflow:hidden}.shot img{width:100%;height:100%;object-fit:contain;display:block}
.shot div{padding:13px 15px 15px;display:grid;gap:5px}.shot strong{font-size:15px}.shot span{font-size:13px;color:#2563eb}.shot code{font-size:11px;color:#647181;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
</style>
</head>
<body>
<header><h1>单页功能截图图库</h1><p>共 ${entries.length} 张。已去掉多页面拼图，每个文件按平台和功能页命名。</p><input class="search" id="search" placeholder="搜索平台、功能或文件名…"></header>
<main><section class="grid" id="grid">${cards}</section></main>
<script>
const input=document.querySelector('#search');const cards=[...document.querySelectorAll('.shot')];
input.addEventListener('input',()=>{const query=input.value.trim().toLowerCase();cards.forEach(card=>{card.hidden=query&&!card.dataset.search.includes(query)})});
</script>
</body>
</html>
`;

const markdown = `# 单页功能截图索引

共 ${entries.length} 张。这个目录剔除了多页面拼图，保留每个功能页一张独立图片。

${entries
  .map((entry) => `- [${entry.platform} · ${entry.functionName} · ${entry.title}](../${entry.target})`)
  .join("\n")}
`;

fs.writeFileSync(singleGalleryPath, html);
fs.writeFileSync(singleIndexPath, markdown);
fs.writeFileSync(
  path.join(outputRoot, "single-page-screenshots-manifest.json"),
  JSON.stringify({ total: entries.length, excludedGroups: [...excludedGroups], entries }, null, 2),
);

console.log(JSON.stringify({ total: entries.length, singleRoot, singleGalleryPath, singleIndexPath }, null, 2));

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}
