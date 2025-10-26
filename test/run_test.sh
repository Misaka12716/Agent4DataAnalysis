#!/bin/bash
# 运行空气湿度与银屑病相关性分析测试
# Usage: bash test/run_test.sh

set -e

echo "=================================="
echo "运行 WEL 测试案例"
echo "=================================="
echo ""

# 进入项目根目录
cd "$(dirname "$0")/.."

# 运行测试
echo "开始运行测试..."
python test/test_humidity_psoriasis_analysis.py

echo ""
echo "=================================="
echo "测试完成！"
echo "=================================="
echo ""
echo "输出文件位置:"
echo "  - test/output/humidity_psoriasis_wel.json"
echo "  - test/output/humidity_psoriasis_test_data.json"
echo "  - test/output/humidity_psoriasis_pdl.json"
echo ""

