import sys
import os
import asyncio
import aiohttp
import json
import base64
import re
from typing import AsyncGenerator, List, Tuple
import hashlib  # 新增：用于生成文件唯一标识

sys.path.append("/data/agent_platform/src")

import streamlit as st
from utils.config import (
    UPLOAD_FOLDER,
    DOWNLOAD_FOLDER,
    SUPPORTED_MODELS,
    DEFAULT_MODEL,
)

# 页面配置
st.set_page_config(page_title="数据分析Agent", page_icon="🤖", layout="wide")
st.title("🤖 数据分析Agent对话界面")

# 初始化Session State，用于保存文件信息
if "saved_file_paths" not in st.session_state:
    st.session_state.saved_file_paths = []
if "file_info" not in st.session_state:
    st.session_state.file_info = "No files uploaded"
if "uploaded_files_summary" not in st.session_state:
    st.session_state.uploaded_files_summary = []
if "processed_files" not in st.session_state:  # 新增：记录已处理的文件标识
    st.session_state.processed_files = set()

# Server API配置
API_BASE_URL = "http://localhost:52716"
WORKFLOW_API_ENDPOINT = f"{API_BASE_URL}/run-workflow"
FILE_INFO_API_ENDPOINT = f"{API_BASE_URL}/upload-file-info"


# -------------------------- 新增：生成文件唯一标识 --------------------------
def get_file_identifier(file) -> str:
    """生成文件的唯一标识（文件名+大小+内容哈希）"""
    # 重置文件指针到开头
    file.seek(0)
    # 计算文件内容的哈希
    content_hash = hashlib.md5(file.read()).hexdigest()
    # 重置文件指针
    file.seek(0)
    # 生成唯一标识
    return f"{file.name}_{file.size}_{content_hash}"


# -------------------------- 原有工具函数保持不变 --------------------------
def parse_think_blocks(content: str) -> List[Tuple[str, str, str]]:
    blocks = []
    remaining = content
    while "<think>" in remaining and "</think>" in remaining:
        before, rest = remaining.split("<think>", 1)
        think_content, after = rest.split("</think>", 1)
        blocks.append((before, think_content.strip(), ""))
        remaining = after
    if remaining:
        blocks.append((remaining, "", ""))
    return blocks


def file_to_base64(file) -> str:
    try:
        content = file.read()
        return base64.b64encode(content).decode("utf-8")
    except Exception as e:
        st.warning(f"文件{file.name}编码失败：{str(e)}")
        return ""


def save_uploaded_file(uploaded_file, save_dir=UPLOAD_FOLDER):
    os.makedirs(save_dir, exist_ok=True)
    original_name = uploaded_file.name
    name, ext = os.path.splitext(original_name)
    save_path = os.path.join(save_dir, original_name)
    counter = 1
    while os.path.exists(save_path):
        save_path = os.path.join(save_dir, f"{name}_{counter}{ext}")
        counter += 1
    with open(save_path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return save_path


async def parse_sse_stream(
    response: aiohttp.ClientResponse,
) -> AsyncGenerator[dict, None]:
    buffer = ""
    async for chunk in response.content.iter_any():
        if chunk:
            buffer += chunk.decode("utf-8")
            while "\n\n" in buffer:
                line, buffer = buffer.split("\n\n", 1)
                line = line.strip()
                if line.startswith("data: "):
                    data_str = line[6:].strip()
                    if data_str and data_str != "[DONE]":
                        try:
                            yield json.loads(data_str)
                        except json.JSONDecodeError as e:
                            st.warning(f"SSE数据解析失败：{e} | 原始数据：{data_str}")
                            yield {"type": "unknown", "data": data_str}


# -------------------------- 新增：获取后端文件详细信息 --------------------------
async def fetch_file_details(file_paths: List[str]) -> List[dict]:
    """调用后端接口获取文件详细信息"""
    try:
        async with aiohttp.ClientSession() as session:
            payload = {"file_paths": file_paths}
            async with session.post(
                FILE_INFO_API_ENDPOINT,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    return result.get("file_details", [])
                else:
                    st.error(f"获取文件信息失败：状态码 {response.status}")
                    return []
    except Exception as e:
        st.error(f"调用文件信息接口失败：{str(e)}")
        return []


# -------------------------- 原有异步工作流调用保持不变 --------------------------
async def call_workflow_api(input_data: str, file_info: str):
    with st.chat_message("user"):
        st.markdown(input_data)
        if file_info and file_info != "No files uploaded":
            st.markdown("📁 已上传文件信息：")
            st.markdown(file_info.replace("\n\n上传文件信息：\n", ""))

    assistant_placeholder = st.empty()
    status_placeholder = st.empty()

    normal_content = ""
    think_content = ""
    think_blocks = []
    in_think_block = False
    llm_full_content = ""
    final_workflow_result = ""
    planner_final_data = None

    try:
        async with aiohttp.ClientSession() as session:
            payload = {"input_data": input_data, "file_info": file_info}
            status_placeholder.info("正在启动工作流...")

            async with session.post(
                WORKFLOW_API_ENDPOINT,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=300),
            ) as response:
                if response.status != 200:
                    raise Exception(f"API请求失败：{response.status}")

                status_placeholder.success("工作流已启动，正在处理中...")

                async for data in parse_sse_stream(response):
                    if data.get("type") == "workflow_status":
                        status_placeholder.info(
                            f"🔄 全局状态：{data.get('message', '')}"
                        )
                    elif data.get("type") == "workflow_stage_start":
                        status_placeholder.info(
                            f"🚀 阶段开始：{data.get('stage', '未知阶段')} - {data.get('message', '')}"
                        )
                    elif data.get("type") == "workflow_stage_complete":
                        status_placeholder.success(
                            f"🏁 阶段完成：{data.get('stage', '未知阶段')} - {data.get('message', '')}"
                        )
                    elif data.get("type") == "planner_step":
                        planner_data = data.get("data", {})
                        planner_final_data = (
                            planner_data
                            if planner_data.get("type") == "stage_result"
                            else planner_final_data
                        )

                        if planner_data.get("type") == "status":
                            status_placeholder.info(
                                f"🔍 Planner状态：{planner_data.get('message', '')}"
                            )
                        elif planner_data.get("type") == "step_start":
                            status_placeholder.info(
                                f"📌 步骤{planner_data.get('step', '')}开始：{planner_data.get('name', '')} - {planner_data.get('description', '')}"
                            )
                        elif planner_data.get("type") == "step_complete":
                            status_placeholder.success(
                                f"✅ 步骤{planner_data.get('step', '')}完成：{planner_data.get('name', '')}"
                            )
                        elif planner_data.get("type") == "task_start":
                            status_placeholder.info(
                                f"🔧 子任务开始：{planner_data.get('task_name', '')}（ID：{planner_data.get('task_id', '')}）"
                            )
                        elif planner_data.get("type") == "task_complete":
                            status_placeholder.success(
                                f"✅ 子任务完成：{planner_data.get('task_name', '')}（ID：{planner_data.get('task_id', '')}）"
                            )
                        elif (
                            planner_data.get("type") == "content"
                            and planner_data.get("content_type") == "llm_chunk"
                        ):
                            chunk = planner_data.get("content", "")
                            llm_full_content += chunk

                            if "<think>" in chunk:
                                parts = chunk.split("<think>", 1)
                                if parts[0]:
                                    if in_think_block:
                                        think_content += parts[0]
                                    else:
                                        normal_content += parts[0]
                                in_think_block = True
                                if len(parts) > 1:
                                    think_content += parts[1]
                            elif "</think>" in chunk and in_think_block:
                                parts = chunk.split("</think>", 1)
                                think_content += parts[0]
                                think_blocks.append((normal_content, think_content))
                                normal_content = ""
                                think_content = ""
                                in_think_block = False
                                if len(parts) > 1:
                                    normal_content += parts[1]
                            else:
                                if in_think_block:
                                    think_content += chunk
                                else:
                                    normal_content += chunk

                            with assistant_placeholder.container():
                                with st.chat_message("assistant"):
                                    with st.expander("📝 思考过程", expanded=False):
                                        for i, (
                                            block_normal,
                                            block_think,
                                        ) in enumerate(think_blocks):
                                            if block_normal:
                                                st.markdown(f"**普通内容 #{i+1}：**")
                                                st.markdown(block_normal)
                                            with st.expander(
                                                f"💭 思考内容 #{i+1}", expanded=False
                                            ):
                                                st.markdown(f"> {block_think}")
                                        if normal_content:
                                            st.markdown("**当前内容：**")
                                            st.markdown(normal_content)
                                        if in_think_block and think_content:
                                            with st.expander(
                                                "💭 当前思考中...", expanded=False
                                            ):
                                                st.markdown(f"> {think_content}")
                                    st.markdown("*Agent正在思考...*")
                    elif data.get("type") == "workflow_final_result":
                        assistant_placeholder.empty()
                        summary = data.get("summary", {})
                        planner_result = summary.get("planner_result", {})
                        worker_result = summary.get("worker_result", {})
                        reporter_result = summary.get("reporter_result", {})

                        final_workflow_result = "## 工作流执行完成\n\n"
                        final_workflow_result += "### 1. Planner结果概要：\n"
                        final_workflow_result += (
                            f"- 原始需求：{planner_result.get('输入需求', 'N/A')}\n"
                        )
                        final_workflow_result += f"- 结构化需求目标：{planner_result.get('结构化需求', {}).get('goal', 'N/A')}\n"
                        final_workflow_result += f"- 子任务数量：{len(planner_result.get('任务分配结果', {}).get('tasks', []))}\n"
                        final_workflow_result += f"- 工具匹配任务数：{len(planner_result.get('工具匹配结果', []))}\n"
                        final_workflow_result += f"- Planner执行状态：{'✅ 成功' if planner_result.get('执行成功') else '❌ 失败'}\n\n"

                        final_workflow_result += "### 2. Worker结果概要：\n"
                        final_workflow_result += (
                            f"- 状态：{worker_result.get('status', 'N/A')}\n"
                        )
                        final_workflow_result += (
                            f"- 信息：{worker_result.get('message', 'N/A')}\n\n"
                        )

                        final_workflow_result += "### 3. Reporter结果概要：\n"
                        final_workflow_result += (
                            f"- 状态：{reporter_result.get('status', 'N/A')}\n"
                        )
                        final_workflow_result += f"- 最终报告：{reporter_result.get('final_report', 'N/A')}\n\n"
                        final_workflow_result += f"### 整体执行状态：{'✅ 成功' if summary.get('success') else '❌ 失败'}\n"

                        with assistant_placeholder.container():
                            with st.chat_message("assistant"):
                                if in_think_block and think_content:
                                    think_blocks.append((normal_content, think_content))
                                with st.expander("📝 查看所有思考过程", expanded=False):
                                    for i, (block_normal, block_think) in enumerate(
                                        think_blocks
                                    ):
                                        if block_normal:
                                            st.markdown(f"**普通内容 #{i+1}：**")
                                            st.markdown(block_normal)
                                        with st.expander(
                                            f"💭 思考内容 #{i+1}", expanded=False
                                        ):
                                            st.markdown(block_think)
                                st.markdown(final_workflow_result)
                                with st.expander("🔍 查看完整工作流数据"):
                                    st.json(data)
                    elif data.get("type") == "workflow_error":
                        assistant_placeholder.empty()
                        error_msg = f"❌ 工作流错误：{data.get('message', '未知错误')}（详情：{data.get('error', '')}）"
                        status_placeholder.error(error_msg)
                        with assistant_placeholder.container():
                            with st.chat_message("assistant"):
                                with st.expander("📝 查看LLM思考过程", expanded=False):
                                    for i, (block_normal, block_think) in enumerate(
                                        think_blocks
                                    ):
                                        if block_normal:
                                            st.markdown(f"**普通内容 #{i+1}：**")
                                            st.markdown(block_normal)
                                        with st.expander(
                                            f"💭 思考内容 #{i+1}", expanded=False
                                        ):
                                            st.markdown(block_think)
                                    if in_think_block and think_content:
                                        with st.expander(
                                            "💭 未完成的思考", expanded=False
                                        ):
                                            st.markdown(think_content)
                                st.error(error_msg)
                    elif data.get("type") == "workflow_ended":
                        status_placeholder.success("✅ 工作流执行结束！")
    except Exception as e:
        assistant_placeholder.empty()
        error_msg = f"调用API失败：{str(e)}"
        status_placeholder.error(error_msg)
        with assistant_placeholder.container():
            with st.chat_message("assistant"):
                with st.expander("📝 查看LLM思考过程", expanded=False):
                    for i, (block_normal, block_think) in enumerate(think_blocks):
                        if block_normal:
                            st.markdown(f"**普通内容 #{i+1}：**")
                            st.markdown(block_normal)
                        with st.expander(f"💭 思考内容 #{i+1}", expanded=False):
                            st.markdown(block_think)
                    if in_think_block and think_content:
                        with st.expander("💭 未完成的思考", expanded=False):
                            st.markdown(think_content)
                st.error(error_msg)
    status_placeholder.empty()


# -------------------------- 文件上传处理（修复重复保存问题） --------------------------
uploaded_files = st.file_uploader(
    "上传文件（支持多个）",
    type=["csv", "xlsx", "json", "txt", "pdf", "png", "jpg"],
    accept_multiple_files=True,
    key="file_uploader",  # 新增：添加唯一key
)

# 处理新上传的文件（添加去重逻辑）
if uploaded_files and len(uploaded_files) > 0:
    new_saved_paths = []
    new_file_summary = []
    new_file_identifiers = []

    # 遍历上传的文件，只处理新文件
    for file in uploaded_files:
        file_id = get_file_identifier(file)

        # 如果文件已经处理过，跳过
        if file_id in st.session_state.processed_files:
            continue

        try:
            saved_path = save_uploaded_file(file)
            new_saved_paths.append(saved_path)
            summary = f"- 文件名：{file.name}（类型：{file.type}，大小：{file.size}字节）\n  保存路径：{saved_path}"
            new_file_summary.append(summary)
            new_file_identifiers.append(file_id)
            st.success(f"文件 {file.name} 已成功上传并保存")
        except Exception as e:
            st.error(f"保存文件 {file.name} 失败：{str(e)}")

    # 如果有新文件需要处理
    if new_saved_paths:
        # 获取详细文件信息
        with st.spinner("正在获取文件详细信息..."):
            detailed_info = asyncio.run(fetch_file_details(new_saved_paths))

        # 构建详细信息字符串
        detailed_sections = []
        for idx, details in enumerate(detailed_info):
            if details.get("success"):
                section = f"### 📄 文件 {len(st.session_state.saved_file_paths)+idx+1}: {details['file_name']}\n"
                section += f"- **路径**：{details['file_path']}\n"
                section += f"- **类型**：{details['file_type']}\n"
                section += f"- **大小**：{details['file_size']} 字节 ({round(details['file_size']/1024, 2)} KB)\n"
                section += f"- **最后修改**：{details['modified_time']}\n"

                # 补充特定类型信息
                if "line_count" in details:
                    section += f"- **行数**：{details['line_count']}（字符数：{details['char_count']}）\n"
                if "csv_columns" in details:
                    section += f"- **CSV结构**：{details['csv_columns']}列，表头：{', '.join(details['csv_header'])}\n"
                    section += f"- **数据行数**：{details['csv_data_rows']}\n"
                if "sheet_count" in details:
                    section += f"- **Excel结构**：{details['sheet_count']}个工作表 ({', '.join(details['sheet_names'])})\n"
                    for sheet_name, sheet_detail in details.get(
                        "sheet_details", {}
                    ).items():
                        section += f"  - {sheet_name}：{sheet_detail['row_count']}行 x {sheet_detail['column_count']}列\n"
                if "page_count" in details:
                    section += f"- **PDF信息**：{details['page_count']}页，标题：{details['title']}（作者：{details['author']}）\n"
                if "image_size" in details:
                    section += f"- **图片信息**：{details['image_size']}像素，格式：{details['image_format']}\n"

                detailed_sections.append(section)
            else:
                detailed_sections.append(
                    f"### 📄 文件 {len(st.session_state.saved_file_paths)+idx+1}: {details['file_path']}\n- ❌ 解析失败：{details['error']}\n"
                )

        # 更新Session State
        st.session_state.saved_file_paths.extend(new_saved_paths)
        st.session_state.uploaded_files_summary.extend(new_file_summary)
        st.session_state.processed_files.update(
            new_file_identifiers
        )  # 记录已处理的文件

        if st.session_state.file_info == "No files uploaded":
            st.session_state.file_info = "\n\n## 📁 上传文件详细信息\n" + "\n\n".join(
                detailed_sections
            )
        else:
            st.session_state.file_info += "\n\n" + "\n\n".join(detailed_sections)

# 显示已上传文件信息
if st.session_state.uploaded_files_summary:
    st.subheader("📋 已上传文件列表")
    for summary in st.session_state.uploaded_files_summary:
        st.markdown(summary)

    with st.expander("🔍 查看文件详细信息", expanded=False):
        st.markdown(st.session_state.file_info)

# -------------------------- 用户指令输入（触发分析） --------------------------
user_input = st.chat_input(
    "输入你的分析指令（例如：分析这些数据的趋势、统计销售额等）..."
)
if user_input:
    #     if st.session_state.file_info == "No files uploaded":
    #         st.warning("⚠️ 尚未上传任何文件，请先上传文件再输入分析指令")
    #     else:
    with st.spinner("Agent正在执行分析..."):
        asyncio.run(call_workflow_api(user_input, st.session_state.file_info))

# -------------------------- 功能按钮 --------------------------
col1, col2 = st.columns(2)
with col1:
    if st.button("健康检查", type="secondary"):

        async def check_health():
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"{API_BASE_URL}/health") as response:
                        if response.status == 200:
                            health_data = await response.json()
                            st.success(
                                f"✅ 服务健康：{json.dumps(health_data, indent=2)}"
                            )
                        else:
                            st.error(f"❌ 服务异常：状态码 {response.status}")
            except Exception as e:
                st.error(f"❌ 连接失败：{str(e)}")

        asyncio.run(check_health())

with col2:
    if st.button("清空所有内容", type="secondary"):
        # 清空Session State
        st.session_state.saved_file_paths = []
        st.session_state.file_info = "No files uploaded"
        st.session_state.uploaded_files_summary = []
        st.session_state.processed_files = set()  # 清空已处理文件记录
        st.rerun()

with st.sidebar:
    st.subheader("API配置")
    api_url = st.text_input("API服务器地址", value=API_BASE_URL)
    if st.button("测试连接"):

        async def test_connection():
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"{api_url}/health", timeout=5) as response:
                        if response.status == 200:
                            st.success("✅ 连接成功！")
                            global API_BASE_URL, WORKFLOW_API_ENDPOINT, FILE_INFO_API_ENDPOINT
                            API_BASE_URL = api_url
                            WORKFLOW_API_ENDPOINT = f"{API_BASE_URL}/run-workflow"
                            FILE_INFO_API_ENDPOINT = f"{API_BASE_URL}/upload-file-info"
                        else:
                            st.error(f"❌ 连接失败：状态码 {response.status}")
            except Exception as e:
                st.error(f"❌ 无法连接：{str(e)}")

        asyncio.run(test_connection())

    st.subheader("文件存储信息")
    st.write(f"上传目录：`{UPLOAD_FOLDER}`")
    if os.path.exists(UPLOAD_FOLDER):
        uploaded_files_count = len(os.listdir(UPLOAD_FOLDER))
        st.write(f"目录中文件数：{uploaded_files_count}")

    if st.button("清空上传目录", type="tertiary"):
        try:
            import shutil

            for filename in os.listdir(UPLOAD_FOLDER):
                file_path = os.path.join(UPLOAD_FOLDER, filename)
                if os.path.isfile(file_path):
                    os.unlink(file_path)
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path)
            st.success("✅ 上传目录已清空")
        except Exception as e:
            st.error(f"❌ 清空失败：{str(e)}")

with st.expander("调试信息", expanded=False):
    st.write("当前配置：")
    st.json(
        {
            "api_base_url": API_BASE_URL,
            "workflow_endpoint": WORKFLOW_API_ENDPOINT,
            "file_info_endpoint": FILE_INFO_API_ENDPOINT,
            "upload_folder": UPLOAD_FOLDER,
            "supported_file_types": ["csv", "xlsx", "json", "txt", "pdf", "png", "jpg"],
            "session_state": {
                "saved_file_paths_count": len(st.session_state.saved_file_paths),
                "has_file_info": st.session_state.file_info != "No files uploaded",
                "processed_files_count": len(st.session_state.processed_files),
            },
        }
    )
