import argparse
import sys
from pathlib import Path

# Add project root to sys.path to ensure imports work
sys.path.insert(0, str(Path(__file__).parent))

from video_insight.config import config
from video_insight.downloader import run_downloader
from video_insight.video_processor import process_video_folder
from video_insight.ai_analyzer import run_analyzer
from video_insight.feishu_syncer import run_syncer

def main():
    parser = argparse.ArgumentParser(description="Video Insight Pipeline CLI")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Command: download
    subparsers.add_parser("download", help="Download videos from Feishu Bitable")

    # Command: process
    subparsers.add_parser("process", help="Process videos (VAD, ASR, Contact Sheets)")

    # Command: analyze
    subparsers.add_parser("analyze", help="Analyze videos using AI (Qwen-VL)")

    # Command: sync
    subparsers.add_parser("sync", help="Sync results back to Feishu Bitable")

    # Command: all
    subparsers.add_parser("all", help="Run the full pipeline")

    args = parser.parse_args()

    if args.command == "download":
        print("=== Step 1: Downloading Videos ===")
        run_downloader()
        
    elif args.command == "process":
        print("=== Step 2: Processing Videos ===")
        process_video_folder(config.OUTPUT_DIR, config.RESULT_DIR)
        
    elif args.command == "analyze":
        print("=== Step 3: AI Analysis ===")
        run_analyzer()
        
    elif args.command == "sync":
        print("=== Step 4: Syncing to Feishu ===")
        run_syncer()
        
    elif args.command == "all":
        print("=== Starting Full Pipeline ===")
        
        print("\n>>> [1/4] Downloading Videos...")
        run_downloader()
        
        print("\n>>> [2/4] Processing Videos...")
        process_video_folder(config.OUTPUT_DIR, config.RESULT_DIR)
        
        print("\n>>> [3/4] AI Analysis...")
        run_analyzer()
        
        print("\n>>> [4/4] Syncing to Feishu...")
        run_syncer()
        
        print("\n=== Full Pipeline Completed ===")
        
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
