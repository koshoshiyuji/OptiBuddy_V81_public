"""
シナリオエクスポートスクリプト

データベースに保存されているシナリオをJSON形式でファイルに出力します。
他環境へのインポートやバックアップ、バージョン管理に使用できます。

使用方法:
    # 単一シナリオのエクスポート
    python Backend/dsl_repository/export_scenario.py --id 1
    
    # 複数シナリオのエクスポート
    python Backend/dsl_repository/export_scenario.py --id 1 2 3
    
    # 全シナリオのエクスポート
    python Backend/dsl_repository/export_scenario.py --all
    
    # 出力先ディレクトリを指定
    python Backend/dsl_repository/export_scenario.py --id 1 --output ./exported
"""

import argparse
import json
import re
from pathlib import Path
from typing import List, Optional

from repository import DslRepository


def sanitize_filename(name: str) -> str:
    """
    ファイル名として安全な文字列に変換
    
    Args:
        name: 元のシナリオ名
        
    Returns:
        サニタイズされたファイル名
    """
    # スペースをアンダースコアに変換
    name = name.replace(" ", "_")
    # 英数字、アンダースコア、ハイフン以外を削除
    name = re.sub(r'[^\w\-]', '', name)
    return name.lower()


def export_scenario(
    scenario_id: int,
    output_dir: Path,
    repo: DslRepository
) -> Optional[str]:
    """
    指定されたシナリオをJSONファイルとしてエクスポート
    
    Args:
        scenario_id: エクスポートするシナリオのID
        output_dir: 出力先ディレクトリ
        repo: DslRepositoryインスタンス
        
    Returns:
        エクスポートされたファイルパス（失敗時はNone）
    """
    try:
        # シナリオを取得
        scenario = repo.get_scenario(scenario_id)
        
        if scenario is None:
            print(f"❌ シナリオID {scenario_id} が見つかりません")
            return None
        
        # ファイル名を生成
        safe_name = sanitize_filename(scenario["name"])
        filename = f"{safe_name}.json"
        output_path = output_dir / filename
        
        # DSL JSONのみを出力（メタデータは除外）
        dsl_json = scenario["dsl_json"]
        
        # JSONファイルに書き込み
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(dsl_json, f, indent=2, ensure_ascii=False)
        
        print(f"✅ エクスポート成功: {scenario['name']}")
        print(f"   ID: {scenario_id}")
        print(f"   ファイル: {output_path}")
        print(f"   Tag: {scenario.get('tag', 'N/A')}")
        print(f"   Domain: {scenario.get('domain', 'N/A')}")
        
        return str(output_path)
        
    except Exception as e:
        print(f"❌ エクスポート失敗 (ID: {scenario_id}): {e}")
        return None


def export_all_scenarios(output_dir: Path, repo: DslRepository) -> List[str]:
    """
    全シナリオをエクスポート
    
    Args:
        output_dir: 出力先ディレクトリ
        repo: DslRepositoryインスタンス
        
    Returns:
        エクスポートされたファイルパスのリスト
    """
    scenarios = repo.list_scenarios(is_active=True)
    
    if not scenarios:
        print("⚠️  エクスポート可能なシナリオがありません")
        return []
    
    print(f"\n📦 {len(scenarios)} 件のシナリオをエクスポートします...\n")
    
    exported_files = []
    for scenario in scenarios:
        file_path = export_scenario(scenario["id"], output_dir, repo)
        if file_path:
            exported_files.append(file_path)
        print()  # 空行を追加
    
    return exported_files


def main():
    """メイン処理"""
    parser = argparse.ArgumentParser(
        description="OptiBuddy シナリオエクスポートツール",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  # シナリオID 1 をエクスポート
  python export_scenario.py --id 1
  
  # シナリオID 1, 2, 3 をエクスポート
  python export_scenario.py --id 1 2 3
  
  # 全シナリオをエクスポート
  python export_scenario.py --all
  
  # 出力先を指定
  python export_scenario.py --id 1 --output ./exported
        """
    )
    
    parser.add_argument(
        "--id",
        type=int,
        nargs="+",
        help="エクスポートするシナリオのID（複数指定可）"
    )
    
    parser.add_argument(
        "--all",
        action="store_true",
        help="全シナリオをエクスポート"
    )
    
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="出力先ディレクトリ（デフォルト: Backend/dsl_repository/scenarios/）"
    )
    
    args = parser.parse_args()
    
    # 引数チェック
    if not args.all and not args.id:
        parser.error("--id または --all のいずれかを指定してください")
    
    # 出力先ディレクトリを設定
    if args.output:
        output_dir = Path(args.output)
    else:
        # デフォルトは scenarios/ ディレクトリ
        output_dir = Path(__file__).parent / "scenarios"
    
    # 出力先ディレクトリを作成
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # リポジトリを初期化
    repo = DslRepository()
    
    print("=" * 70)
    print("  OptiBuddy シナリオエクスポート")
    print("=" * 70)
    print(f"出力先: {output_dir.absolute()}\n")
    
    # エクスポート実行
    if args.all:
        exported_files = export_all_scenarios(output_dir, repo)
    else:
        exported_files = []
        for scenario_id in args.id:
            file_path = export_scenario(scenario_id, output_dir, repo)
            if file_path:
                exported_files.append(file_path)
            print()  # 空行を追加
    
    # 結果サマリー
    print("=" * 70)
    print(f"  エクスポート完了: {len(exported_files)} 件")
    print("=" * 70)
    
    if exported_files:
        print("\nエクスポートされたファイル:")
        for file_path in exported_files:
            print(f"  • {file_path}")
    
    print()


if __name__ == "__main__":
    main()
