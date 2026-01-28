import argparse
import sys
from pathlib import Path

from config import config
from downloader import run_downloader
from video_processor import process_video_folder
from ai_analyzer import run_analyzer
# from feishu_syncer import sync_data


def main():
    parser = argparse.ArgumentParser(description="视频洞察分析管线 CLI")
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # 命令: download
    subparsers.add_parser("download", help="从飞书多维表格下载视频")

    # 命令: process
    subparsers.add_parser("process", help="处理视频 (VAD, ASR, 拼图)")

    # 命令: analyze
    subparsers.add_parser("analyze", help="使用 AI (Qwen-VL) 分析视频")

    # 命令: sync
    subparsers.add_parser("sync", help="同步结果回飞书多维表格")

    # 命令: all
    subparsers.add_parser("all", help="运行完整管线")

    args = parser.parse_args()

    if args.command == "download":
        print("=== 步骤 1: 下载视频 ===")
        run_downloader()

    elif args.command == "process":
        print("=== 步骤 2: 处理视频 ===")
        process_video_folder(config.DOWNLOAD_DIR, config.OUTPUT_DIR)

    elif args.command == "analyze":
        print("=== 步骤 3: AI 分析 ===")
        run_analyzer()

    elif args.command == "sync":
        print("=== 步骤 4: 同步到飞书 ===")
        # sync_data()

    elif args.command == "all":
        print("=== 开始完整管线 ===")

        print("\n>>> [1/4] 正在下载视频...")
        run_downloader()

        print("\n>>> [2/4] 正在处理视频...")
        process_video_folder(config.OUTPUT_DIR, config.RESULT_DIR)

        print("\n>>> [3/4] AI 分析...")
        run_analyzer()

        print("\n>>> [4/4] 正在同步到飞书...")
        # sync_data()

        print("\n=== 完整管线执行完毕 ===")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
