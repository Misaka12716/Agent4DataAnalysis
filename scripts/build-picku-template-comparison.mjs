import fs from "node:fs";
import path from "node:path";

const researchRoot = process.argv[2];
if (!researchRoot) {
  throw new Error("Usage: node scripts/build-picku-template-comparison.mjs <research-root>");
}

const currentRoot = path.join(researchRoot, "screenshots/current/pages");
const singleRoot = path.join(researchRoot, "single-page-screenshots");
const reportRoot = path.join(researchRoot, "report");
const outputPath = path.join(reportRoot, "PICKU_TEMPLATE_COMPARISON.html");

const refs = Object.fromEntries(
  fs
    .readdirSync(singleRoot)
    .filter((name) => /\.(png|jpe?g|webp)$/i.test(name))
    .map((name) => [name, path.join(singleRoot, name)]),
);

const sample = {
  aiSuggestions: ref("Lateral 文本建议", "功能界面-ai-阅读、写作与深度研究-ai-suggestions-5.-get-text-suggestions.png", "轻量建议浮层，适合辅助编辑"),
  aminerProfile: ref("AMiner 研究者档案", "补抓平台与受限页面-03-aminer-profile-03-aminer-profile.png", "学者/方向档案的信息分组"),
  aminerSearch: ref("AMiner 检索结果", "补抓平台与受限页面-02-aminer-search-02-aminer-search.png", "搜索结果和筛选密度"),
  anaraModel: ref("Anara Model Council", "外部平台功能页-45-anara-model-council-45-anara-model-council.png", "模型/专家资源的横向评审式布局"),
  benchlingPlatform: ref("Benchling 平台资产", "外部平台功能页-60-benchling-platform-60-benchling-platform.png", "科研项目、对象、协作的产品级表格气质"),
  benchlingNotebook: ref("Benchling 实验记录", "外部平台功能页-61-benchling-notebook-61-benchling-notebook.png", "实验记录、步骤和结果的工作台结构"),
  benchlingRegistry: ref("Benchling 生物注册表", "功能界面-数据分析与实验协作-biological-registry-biologics-sample-registration-system-for-large-m.png", "档案、字段、状态和审核信息组织方式"),
  cbkSearch: ref("CBK 医学知识检索", "重点参考站点二级页面-cbk-search-cbk-search.png", "机构型医学视觉，低干扰、可信赖"),
  chunkrExtract: ref("Chunkr 数据抽取示例", "功能界面-数据分析与实验协作-data-extraction-examples-extract-examples-chunkr.png", "抽取结果、字段和校验样例"),
  chunkrTask: ref("Chunkr 任务系统", "功能界面-数据分析与实验协作-task-dashboard-documentation-task-system-overview-chunkr.png", "任务状态、阶段和批处理视角"),
  connectedGraph: ref("Connected Papers 图谱", "功能界面-文献搜索与图谱-citation-graph-attention-is-all-you-need-connected-papers.png", "全画布关系图，适合知识图谱和综述结构"),
  connectedResults: ref("Connected Papers 搜索结果", "功能界面-文献搜索与图谱-paper-search-results-attention-is-all-you-need-connected-papers-sea.png", "图谱前的论文选择列表"),
  datasetPublication: ref("OSF 数据集条目", "功能界面-扩展项目、数据与实验协作-dataset-publication-item-agglomeration-benefits-or-adaptation-pres.png", "数据集详情和元信息组织"),
  deepnoteAi: ref("Deepnote AI 分析", "功能界面-数据分析与实验协作-ai-notebook-documentation-generative-analysis-deepnote-docs.png", "Notebook/AI 分析过程的布局"),
  deepnoteApp: ref("Deepnote 数据应用", "功能界面-数据分析与实验协作-data-app-documentation-data-apps-deepnote-docs.png", "可复现结果块和交互式数据应用"),
  deepnoteComments: ref("Deepnote 协作评论", "功能界面-数据分析与实验协作-collaboration-documentation-comments-deepnote-docs.png", "协作批注与上下文反馈"),
  elicitPaper: ref("Elicit 论文检索", "功能界面-文献搜索与图谱-paper-search-feature-paper-search-elicit-al-for-scientific-researc.png", "研究问题驱动的论文检索入口"),
  elicitReport: ref("Elicit 研究报告", "功能界面-文献搜索与图谱-research-report-feature-reports-elicit-al-for-scientific-research.png", "任务式证据组织，突出来源和产出"),
  explainpaperUpload: ref("Explainpaper 上传工作区", "功能界面-扩展文献发现与引用网络-upload-workspace-explainpaper.png", "文献上传后进入解释/阅读工作区"),
  findinDrawing: ref("Findin 科研绘图", "findin-飞引-findin-登录后功能页-科研绘图-findin-飞引.png", "工具型成果生成，视觉克制"),
  findinHome: ref("Findin 工作台首页", "findin-飞引-登录后工作台-findin-登录后首页.png", "克制白底，按科研任务组织入口"),
  findinLibrary: ref("Findin 我的文献", "findin-飞引-登录后文献库-我的文献-findin-飞引.png", "文献资产和研究集合管理"),
  findinNotes: ref("Findin 笔记摘录", "findin-飞引-findin-登录后功能页-笔记摘录-findin-飞引.png", "摘录、笔记和知识片段管理"),
  findinReading: ref("Findin 文献阅读", "findin-飞引-findin-登录后功能页-how-to-read-a-paper-findin-飞引.png", "正文阅读 + AI 辅助 + 文献上下文"),
  findinReview: ref("Findin 文献综述", "findin-飞引-findin-登录后功能页-ai-文献综述-findin飞引.png", "来源文献到综述产出的清晰路径"),
  findinSettings: ref("Findin 个人设置", "findin-飞引-findin-登录后功能页-个人设置-findin-飞引.png", "设置页应收敛、安静、表单清晰"),
  findinSubjects: ref("Findin 主题分类", "findin-飞引-findin-登录后功能页-主题分类-findin-飞引.png", "研究领域、标签和主题选择方式"),
  hexProduct: ref("Hex 分析产品界面", "外部平台功能页-58-hex-product-58-hex-product.png", "项目式分析台，适合数据、步骤、结果沉淀"),
  hexTemplates: ref("Hex 模板库", "外部平台功能页-59-hex-templates-59-hex-templates.png", "按业务问题组织分析模板"),
  inventoryTable: ref("SciNote 库存表格", "功能界面-数据分析与实验协作-inventory-table-inventory-table-access-column-marked.png-2321×7.png", "密集表格、权限字段和状态列"),
  litmapsWorkspace: ref("Litmaps 文献选择", "功能界面-文献搜索与图谱-article-selection-workspace-litmaps.png", "从论文列表进入关系探索"),
  lkDocking: ref("LKStudio 分子对接结果", "lkstudio-molecular-docking-results-罗柯生信-结果展示.png", "科研结果展示和图像解释"),
  lkMd: ref("LKStudio 动力学结果", "lkstudio-molecular-dynamics-results-罗柯生信-结果展示.png", "多图表科研结果陈列"),
  migoKnowledge: ref("Migo 知识空间", "功能界面-文献搜索与图谱-knowledge-space-migo-觅果-ai-学研助手.png", "知识库式文献组织"),
  migoReader: ref("Migo 文献阅读上传", "功能界面-文献搜索与图谱-paper-reader-upload-migo-觅果-ai-学研助手.png", "文献上传、阅读和 AI 辅助入口"),
  migoValidation: ref("Migo 引用验证", "功能界面-文献搜索与图谱-citation-validation-workspace-migo-觅果-ai-学研助手.png", "引用校验和证据状态"),
  observableInput: ref("Observable 交互式 Notebook", "功能界面-扩展项目、数据与实验协作-interactive-notebook-observable-inputs-observable-documentation.png", "交互参数和结果联动"),
  openKnowledgeMap: ref("Open Knowledge Maps", "功能界面-扩展文献发现与引用网络-interactive-search-results-open-knowledge-maps-your-guide-to-scientific-k.png", "搜索结果到知识地图的过渡"),
  openreadTrending: ref("OpenRead Trending", "外部平台功能页-16-openread-trending-16-openread-trending.png", "趋势/热点内容流"),
  osfContributors: ref("OSF 成员权限", "功能界面-扩展项目、数据与实验协作-contributors-and-permissions-osf-contributor-permissions.png", "项目成员、角色和权限管理"),
  osfFiles: ref("OSF 文件管理", "功能界面-扩展项目、数据与实验协作-project-file-management-osf-project-files-and-folders.png", "项目文件树和资源管理方式"),
  osfWiki: ref("OSF Wiki 编辑器", "功能界面-扩展项目、数据与实验协作-project-wiki-editor-osf-project-wiki-editor.png", "项目内文档和结构化记录"),
  paperpalReference: ref("Paperpal Reference Finder", "功能界面-ai-阅读、写作与深度研究-reference-search-reference-finder-ai-citation-finder,-source-fin.png", "引用检索和证据候选"),
  paperpalSubmission: ref("Paperpal Manuscript Checker", "功能界面-ai-阅读、写作与深度研究-submission-check-manuscript-checker-research-paper-check-&-journ.png", "检查项、建议和状态反馈"),
  paperpalTemplates: ref("Paperpal 写作模板", "功能界面-ai-阅读、写作与深度研究-templates-generative-ai-for-academic-writing-create-titl.png", "成果类型模板入口"),
  panshiWorkflow: ref("磐石科研流程", "磐石大模型-ai-research-workflow-scienceone-ai-for-science-科研智能平台.png", "可信科研品牌气质和能力层级表达"),
  ponderMindmap: ref("Ponder Mindmap", "外部平台功能页-48-ponder-mindmap-48-ponder-mindmap.png", "思维导图型知识组织"),
  ponderWhiteboard: ref("Ponder Whiteboard", "外部平台功能页-49-ponder-whiteboard-49-ponder-whiteboard.png", "自由画布、便签和节点协作"),
  protocolHistory: ref("SciNote 协议历史", "功能界面-数据分析与实验协作-protocol-version-history-protocol-version-history-modal-open-1.png-1002×.png", "版本历史和记录追溯"),
  qinyanKnowledge: ref("沁言知识库", "外部平台功能页-30-qinyan-knowledge-30-qinyan-knowledge.png", "国内科研工具的知识库入口"),
  readpaperAi: ref("ReadPaper AI 辅写", "三站深度调研-readpaper-ai-辅写-readpaper-ai-辅写.png", "写作区域和资料资产结合"),
  readpaperGroups: ref("ReadPaper 小组管理", "三站深度调研-readpaper-小组管理-readpaper-小组管理.png", "科研小组和协作管理"),
  readpaperLibrary: ref("ReadPaper 文献管理", "三站深度调研-readpaper-文献管理-readpaper-文献管理.png", "固定侧栏 + 资产表格 + 操作入口"),
  readpaperNotes: ref("ReadPaper 笔记管理", "三站深度调研-readpaper-笔记管理-readpaper-笔记管理.png", "文献资产、笔记、摘要的长期管理方式"),
  readpaperSummary: ref("ReadPaper 笔记总结", "三站深度调研-readpaper-笔记：总结-readpaper-笔记：总结.png", "总结型知识资产视图"),
  readpaperTerms: ref("ReadPaper 术语短语", "三站深度调研-readpaper-笔记：单词与短语-readpaper-笔记：单词与短语.png", "细粒度知识条目"),
  rspaceGroups: ref("RSpace 小组协作", "功能界面-扩展项目、数据与实验协作-group-collaboration-rspace-lab-groups-and-sharing.png", "实验室成员和共享空间"),
  rspaceRevisions: ref("RSpace 版本历史", "功能界面-扩展项目、数据与实验协作-document-version-history-rspace-revisions-and-document-history.png", "文档修改历史和追溯"),
  rspaceWorkspace: ref("RSpace 研究工作区", "功能界面-扩展项目、数据与实验协作-research-workspace-the-rspace-research-workspace.png", "实验记录、资产和项目空间"),
  scholarcyAnalysis: ref("Scholarcy 分析流程", "功能界面-数据分析与实验协作-analysis-workflow-assimilate-and-analyse-scholarcy-user-guide.png", "从导入到阅读分析的流程感"),
  scholarcyFlashcard: ref("Scholarcy Flashcard", "功能界面-数据分析与实验协作-flashcard-reading-workflow-supercharge-your-reading-scholarcy-user-guide.png", "卡片式阅读和知识复习"),
  scholarcyImport: ref("Scholarcy 导入流程", "功能界面-数据分析与实验协作-document-import-workflow-easy-import-scholarcy-user-guide.png", "资料导入和文档组织"),
  scienceoneCompass: ref("ScienceOne 文献罗盘", "scienceone-literature-compass-section-磐石-scienceone-ai-for-science-科研智能平台.png", "科研能力包装和方向感"),
  scienceosWorkspace: ref("ScienceOS 工作区", "功能界面-扩展文献发现与引用网络-research-workspace-scienceos.png", "研究空间和文献发现入口"),
  scispaceChat: ref("SciSpace Chat PDF", "外部平台功能页-21-scispace-chat-pdf-21-scispace-chat-pdf.png", "PDF 对话和证据面板结合"),
  scispaceConcepts: ref("SciSpace Concepts", "外部平台功能页-24-scispace-concepts-24-scispace-concepts.png", "概念抽取和知识解释"),
  scispaceExtract: ref("SciSpace Extract", "外部平台功能页-25-scispace-extract-25-scispace-extract.png", "结构化抽取和字段输出"),
  scispaceLiterature: ref("SciSpace Literature Review", "功能界面-ai-阅读、写作与深度研究-literature-review-scispace-literature-review-ai-agent-for-conduc.png", "文献综述任务的 AI Agent 风格"),
  scispaceSearch: ref("SciSpace Search", "外部平台功能页-22-scispace-search-22-scispace-search.png", "科研搜索入口和结果预览"),
  scispaceTemplates: ref("SciSpace 模板中心", "外部平台功能页-20-scispace-templates-20-scispace-templates.png", "工具和模板的分类入口"),
  scispaceWriter: ref("SciSpace Writer", "外部平台功能页-23-scispace-writer-23-scispace-writer.png", "写作编辑器、大纲、引用与导出"),
  semanticAuthor: ref("Semantic Scholar 作者页", "外部平台功能页-36-semantic-author-36-semantic-author.png", "作者/研究方向档案"),
  semanticPaper: ref("Semantic Scholar 论文详情", "外部平台功能页-35-semantic-paper-35-semantic-paper.png", "论文详情、引用和相关信息"),
  semanticSearch: ref("Semantic Scholar 搜索", "外部平台功能页-34-semantic-search-34-semantic-search.png", "搜索、筛选、结果密度更适合文献检索"),
  txyzHome: ref("TXYZ 研究输入工作台", "三站深度调研-txyz-应用首页工作区-txyz-应用首页工作区.png", "大输入区 + 左侧资料上下文 + 轻量结果区"),
  txyzInput: ref("TXYZ 输入与每日精选", "三站深度调研-txyz-输入与每日精选-txyz-输入与每日精选.png", "问题、文件、URL 合并成一个清晰起点"),
  xljAi: ref("小绿鲸 AI 功能页", "外部平台功能页-32-xlj-ai-32-xlj-ai.png", "国内文献阅读工具的 AI 入口"),
};

const pages = [
  page("01-data-analysis.png", "数据分析", "三栏分析工作台", "左侧研究上下文，中间任务与结果，右侧过程/证据。", [sample.txyzHome, sample.hexProduct, sample.deepnoteAi]),
  page("01-data-dialog-process.png", "数据分析", "任务执行过程", "把执行过程做成任务时间线，和结果块绑定。", [sample.elicitReport, sample.chunkrTask, sample.deepnoteComments]),
  page("data-dialog-process.png", "数据分析", "AI 执行态", "应与主数据分析页统一成同一个执行态。", [sample.scispaceChat, sample.migoValidation, sample.txyzInput]),
  page("data-dialog-tools.png", "数据分析", "工具与模板抽屉", "工具入口做成命令/模板抽屉，不要堆成杂项列表。", [sample.scispaceTemplates, sample.paperpalTemplates, sample.xljAi]),
  page("data-dialog-workspace.png", "数据分析", "可复现工作区", "工作区突出可复现步骤、数据预览和结果块。", [sample.hexProduct, sample.observableInput, sample.deepnoteApp]),
  page("data-template.png", "数据分析", "分析模板库", "模板按任务、输入数据、产出物组织。", [sample.hexTemplates, sample.scispaceTemplates, sample.paperpalTemplates]),
  page("02-clinical-dashboard.png", "临床与数据质量", "临床仪表盘", "临床页要学医学/科研数据台的可信感，减少紫色大横幅。", [sample.cbkSearch, sample.benchlingPlatform, sample.benchlingRegistry]),
  page("03-clinical-patient.png", "临床与数据质量", "患者/队列档案", "患者/队列详情应该是档案式界面，包含字段、状态、时间线。", [sample.benchlingRegistry, sample.inventoryTable, sample.rspaceWorkspace]),
  page("04-clinical-reference.png", "临床与数据质量", "医学证据库", "参考范围应像证据库，强调来源和版本。", [sample.cbkSearch, sample.semanticPaper, sample.paperpalReference]),
  page("05-clinical-followup.png", "临床与数据质量", "随访时间线", "随访更适合时间线和事件记录风格。", [sample.benchlingNotebook, sample.rspaceRevisions, sample.protocolHistory]),
  page("06-clinical-risk.png", "临床与数据质量", "风险评估证据", "风险评估应显示变量、解释、证据和建议动作。", [sample.elicitReport, sample.semanticPaper, sample.paperpalSubmission]),
  page("07-clinical-comorbidity.png", "临床与数据质量", "关系分析", "共病关系用小图谱 + 表格证据组合。", [sample.connectedGraph, sample.openKnowledgeMap, sample.cbkSearch]),
  page("08-clinical-correlation.png", "临床与数据质量", "统计结果台", "相关性分析要像统计结果工作台，而不是空表单。", [sample.observableInput, sample.deepnoteApp, sample.scholarcyAnalysis]),
  page("09-clinical-report.png", "临床与数据质量", "报告编辑器", "报告类页面进入成果工作室风格：来源、正文、引用、导出。", [sample.scispaceWriter, sample.readpaperAi, sample.findinReview]),
  page("dq-phi.png", "临床与数据质量", "脱敏审核台", "PHI/脱敏要突出字段命中、规则和审核状态。", [sample.benchlingRegistry, sample.inventoryTable, sample.chunkrExtract]),
  page("dq-qc.png", "临床与数据质量", "质量控制台", "QC 页做成质量分、问题队列、规则表。", [sample.hexProduct, sample.chunkrTask, sample.deepnoteApp]),
  page("dq-timeline.png", "临床与数据质量", "时间线校验", "时间线校验用事件流，不只放输入框。", [sample.protocolHistory, sample.rspaceRevisions, sample.deepnoteComments]),
  page("10-science-home.png", "科研首页", "研究驾驶舱", "首页要看工作台/驾驶舱风格，不看官网首页。", [sample.findinHome, sample.txyzHome, sample.panshiWorkflow]),
  page("13-science-home.png", "科研首页", "研究驾驶舱变体", "两个 science home 建议合并为一个研究驾驶舱。", [sample.scienceoneCompass, sample.scienceosWorkspace, sample.findinLibrary]),
  page("11-science-scheduled-tasks.png", "科研任务", "自动化任务", "定时任务属于项目自动化资产，风格应接近任务/资产管理。", [sample.chunkrTask, sample.elicitReport, sample.benchlingNotebook]),
  page("12-science-hotspot.png", "科研发现", "热点发现", "热点页重点是搜索、筛选、趋势和证据，不是营销卡片。", [sample.openreadTrending, sample.semanticSearch, sample.aminerSearch]),
  page("science-paper-tracking.png", "科研发现", "文献追踪", "文献追踪按规则、结果、更新、保存到项目组织。", [sample.readpaperLibrary, sample.litmapsWorkspace, sample.semanticSearch]),
  page("13-science-pdf-reading.png", "阅读与知识", "PDF 阅读工作台", "阅读页必须用文献/正文/笔记或 AI 三栏同类参考。", [sample.findinReading, sample.readpaperNotes, sample.scispaceChat]),
  page("14-science-method-extraction.png", "阅读与知识", "方法抽取", "方法抽取从文献和证据出发，接方法卡与分析任务。", [sample.scispaceConcepts, sample.migoValidation, sample.elicitReport]),
  page("15-science-review-mindmap.png", "图谱与画布", "图谱画布", "思维导图和知识图谱用全画布风格。", [sample.connectedGraph, sample.ponderMindmap, sample.openKnowledgeMap]),
  page("16-science-figure-studio.png", "成果", "科研绘图", "科研绘图参考成果生成/画布/结果展示，不参考阅读页。", [sample.findinDrawing, sample.lkDocking, sample.ponderWhiteboard]),
  page("17-science-ppt-generation.png", "成果", "PPT/写作生成", "PPT 生成参考写作编辑器和成果资产，而不是数据分析台。", [sample.scispaceWriter, sample.readpaperAi, sample.paperpalTemplates]),
  page("18-science-review-ppt.png", "成果", "综述成果复盘", "综述 PPT 属于成果编辑和引用组织。", [sample.findinReview, sample.scispaceLiterature, sample.connectedGraph]),
  page("profile-delivery.png", "研究设置", "通知与投递设置", "投递/通知收进设置，表单要短、清楚、有状态。", [sample.findinSettings, sample.qinyanKnowledge, sample.readpaperSummary]),
  page("profile-directions.png", "研究设置", "研究方向设置", "研究方向参考主题/标签选择，不参考登录问卷。", [sample.findinSubjects, sample.semanticAuthor, sample.aminerProfile]),
  page("profile-hot.png", "研究设置", "热点订阅设置", "热点偏好作为订阅设置，而不是独立大工作台。", [sample.openreadTrending, sample.semanticSearch, sample.findinSubjects]),
  page("profile-l1.png", "研究设置", "基础画像设置", "画像页要降级成设置型页面。", [sample.findinSettings, sample.findinSubjects, sample.readpaperSummary]),
  page("profile-l2.png", "研究设置", "偏好配置", "L1/L2/L3 改成渐进式设置。", [sample.readpaperTerms, sample.qinyanKnowledge, sample.semanticAuthor]),
  page("profile-l3.png", "研究设置", "高级记忆设置", "高级画像信息折叠，说明用途。", [sample.findinSettings, sample.readpaperNotes, sample.findinNotes]),
  page("profile-memory.png", "研究设置", "长期记忆", "长期记忆可参考笔记/知识资产，不做空表单。", [sample.findinNotes, sample.readpaperSummary, sample.qinyanKnowledge]),
  page("profile-methods.png", "研究设置", "方法偏好", "方法偏好连接方法卡、文献证据和模板。", [sample.scispaceConcepts, sample.migoKnowledge, sample.elicitReport]),
  page("profile-search.png", "研究设置", "搜索偏好", "搜索偏好看检索筛选风格。", [sample.semanticSearch, sample.aminerSearch, sample.elicitPaper]),
  page("projects-overview.png", "项目与资产", "项目总览", "项目页只看资产/协作/表格类参考。", [sample.benchlingPlatform, sample.rspaceWorkspace, sample.hexProduct]),
  page("projects-current.png", "项目与资产", "当前项目", "当前项目应该呈现最近资产、任务、成员和进度。", [sample.osfFiles, sample.readpaperLibrary, sample.rspaceWorkspace]),
  page("projects-assets.png", "项目与资产", "资产列表", "资产页统一文件、数据、模型、成果。", [sample.osfFiles, sample.benchlingRegistry, sample.datasetPublication]),
  page("projects-members.png", "项目与资产", "成员权限", "成员页看协作/权限风格。", [sample.osfContributors, sample.rspaceGroups, sample.readpaperGroups]),
  page("projects-sessions.png", "项目与资产", "会话记录", "会话记录作为项目资产列表。", [sample.deepnoteComments, sample.readpaperNotes, sample.rspaceRevisions]),
  page("projects-tasks.png", "项目与资产", "任务管理", "任务页展示状态、负责人、输入输出资产。", [sample.chunkrTask, sample.benchlingNotebook, sample.elicitReport]),
  page("projects-tree.png", "项目与资产", "项目树", "树结构和资源预览同屏。", [sample.osfFiles, sample.osfWiki, sample.rspaceWorkspace]),
  page("resources-files.png", "项目与资产", "文件资源", "文件资源按项目、来源、处理状态管理。", [sample.osfFiles, sample.scholarcyImport, sample.readpaperLibrary]),
  page("resources-datasets.png", "项目与资产", "数据集资源", "数据集要显示 schema、质量分、最近使用。", [sample.benchlingRegistry, sample.inventoryTable, sample.datasetPublication]),
  page("resources-models.png", "项目与资产", "模型资源", "模型资源显示版本、适用任务、调用记录。", [sample.anaraModel, sample.benchlingPlatform, sample.deepnoteApp]),
];

const missing = [];
for (const item of pages) {
  if (!fs.existsSync(path.join(currentRoot, item.file))) missing.push(`current: ${item.file}`);
  for (const reference of item.references) {
    if (!refs[reference.file]) missing.push(`ref: ${reference.file}`);
  }
}
if (missing.length) {
  throw new Error(`Missing images:\n${missing.join("\n")}`);
}

fs.mkdirSync(reportRoot, { recursive: true });
fs.writeFileSync(outputPath, render());
console.log(JSON.stringify({ outputPath, pages: pages.length }, null, 2));

function ref(title, file, reason) {
  return { title, file, reason };
}

function page(file, module, style, advice, references) {
  return { file, module, style, advice, references };
}

function render() {
  const modules = [...new Set(pages.map((item) => item.module))];
  const styles = [...new Set(pages.map((item) => item.style))];
  return `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PickU 页面风格同类对比</title>
<style>
:root{--ink:#10243e;--muted:#647181;--canvas:#eef2f6;--paper:#fff;--line:#d7dee7;--blue:#2563eb;--teal:#0f766e}
*{box-sizing:border-box}body{margin:0;background:var(--canvas);color:var(--ink);font-family:"Noto Sans CJK SC","Microsoft YaHei",sans-serif}
header{position:sticky;top:0;z-index:5;background:rgba(255,255,255,.96);backdrop-filter:blur(16px);border-bottom:1px solid var(--line);padding:22px 30px 16px}
h1{margin:0 0 7px;font-size:28px}p{margin:0;color:var(--muted)}.toolbar{display:flex;gap:10px;flex-wrap:wrap;margin-top:16px}
input,select{border:1px solid var(--line);border-radius:8px;background:#fff;color:var(--ink);padding:10px 12px;font:inherit}input{flex:1;min-width:320px}
main{padding:24px 30px 60px}.grid{display:grid;gap:24px}.card{background:var(--paper);border:1px solid var(--line);border-radius:12px;overflow:hidden;box-shadow:0 7px 22px rgba(16,36,62,.06)}.card[hidden]{display:none}
.head{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:15px 18px;border-bottom:1px solid var(--line)}.head strong{font-size:18px;margin-right:auto}.pill{font-size:12px;border:1px solid var(--line);border-radius:999px;padding:5px 9px;color:var(--muted);background:#f8fafc}.pill.style{border-color:#bfdbfe;background:#eff6ff;color:var(--blue)}
.compare{display:grid;grid-template-columns:minmax(380px,1fr) minmax(520px,1.42fr);gap:0}.current{border-right:1px solid var(--line)}.sectionTitle{margin:0;padding:12px 14px;border-bottom:1px solid var(--line);font-size:14px}.sectionTitle span{color:var(--muted);font-weight:400}
.imageBox{height:430px;background:#e5ebf2;display:flex;align-items:center;justify-content:center;overflow:hidden}.imageBox img{max-width:100%;max-height:100%;object-fit:contain;display:block}
.refs{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));height:430px}.ref{min-width:0;border-right:1px solid var(--line);display:grid;grid-template-rows:auto 1fr auto}.ref:last-child{border-right:0}.ref h3{margin:0;padding:10px 12px;font-size:13px;border-bottom:1px solid var(--line)}.ref .imageBox{height:auto}.ref p{padding:9px 12px 11px;border-top:1px solid var(--line);font-size:12px;line-height:1.45;color:var(--muted)}
.advice{padding:13px 18px 16px;border-top:1px solid var(--line);font-size:14px;color:var(--muted)}.advice b{color:var(--ink)}
@media(max-width:1180px){.compare{grid-template-columns:1fr}.current{border-right:0;border-bottom:1px solid var(--line)}.refs{height:auto}.ref .imageBox{height:260px}}
@media(max-width:760px){header,main{padding-left:15px;padding-right:15px}.refs{grid-template-columns:1fr}.ref{border-right:0;border-bottom:1px solid var(--line)}.imageBox{height:280px}input{min-width:100%}}
</style>
</head>
<body>
<header>
  <h1>PickU 页面风格同类对比</h1>
  <p>共 ${pages.length} 个当前页面。只保留真实功能界面作参考，过滤掉无效状态页、访问拦截页和纯营销页。</p>
  <div class="toolbar">
    <input id="search" placeholder="搜索当前页面、模块、风格组或参考平台…">
    <select id="module"><option value="all">全部模块</option>${modules.map((value) => `<option>${escapeHtml(value)}</option>`).join("")}</select>
    <select id="style"><option value="all">全部风格组</option>${styles.map((value) => `<option>${escapeHtml(value)}</option>`).join("")}</select>
  </div>
</header>
<main><section class="grid">${pages.map(card).join("\n")}</section></main>
<script>
const search=document.querySelector('#search');const moduleSelect=document.querySelector('#module');const styleSelect=document.querySelector('#style');const cards=[...document.querySelectorAll('.card')];
function apply(){const query=search.value.trim().toLowerCase();const module=moduleSelect.value;const style=styleSelect.value;cards.forEach(card=>{const okQuery=!query||card.dataset.search.includes(query);const okModule=module==='all'||card.dataset.module===module;const okStyle=style==='all'||card.dataset.style===style;card.hidden=!(okQuery&&okModule&&okStyle)})}
[search,moduleSelect,styleSelect].forEach(item=>item.addEventListener('input',apply));
</script>
</body>
</html>`;
}

function card(item, index) {
  const references = item.references;
  const search = `${item.file} ${item.module} ${item.style} ${item.advice} ${references.map((reference) => reference.title).join(" ")}`.toLowerCase();
  return `<article class="card" data-module="${escapeHtml(item.module)}" data-style="${escapeHtml(item.style)}" data-search="${escapeHtml(search)}">
  <div class="head"><strong>${String(index + 1).padStart(2, "0")} · ${escapeHtml(titleFromFile(item.file))}</strong><span class="pill">${escapeHtml(item.module)}</span><span class="pill style">${escapeHtml(item.style)}</span></div>
  <div class="compare">
    <section class="current"><h2 class="sectionTitle">当前 PickU <span>${escapeHtml(item.file)}</span></h2><a class="imageBox" href="${dataUri(path.join(currentRoot, item.file))}" target="_blank"><img loading="lazy" src="${dataUri(path.join(currentRoot, item.file))}" alt="${escapeHtml(item.file)}"></a></section>
    <section><h2 class="sectionTitle">同类型风格样本 <span>不是功能照搬，是页面气质和结构参考</span></h2><div class="refs">${references.map(referenceCard).join("")}</div></section>
  </div>
  <div class="advice"><b>这页该学什么：</b>${escapeHtml(item.advice)}</div>
</article>`;
}

function referenceCard(reference) {
  const src = dataUri(refs[reference.file]);
  return `<a class="ref" href="${src}" target="_blank"><h3>${escapeHtml(reference.title)}</h3><div class="imageBox"><img loading="lazy" src="${src}" alt="${escapeHtml(reference.title)}"></div><p>${escapeHtml(reference.reason)}</p></a>`;
}

function titleFromFile(file) {
  return file.replace(/\.(png|jpe?g|webp)$/i, "").replaceAll("-", " ");
}

function dataUri(imagePath) {
  const extension = path.extname(imagePath).toLowerCase();
  const mime = extension === ".jpg" || extension === ".jpeg" ? "image/jpeg" : extension === ".webp" ? "image/webp" : "image/png";
  return `data:${mime};base64,${fs.readFileSync(imagePath).toString("base64")}`;
}

function escapeHtml(value) {
  return String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}
