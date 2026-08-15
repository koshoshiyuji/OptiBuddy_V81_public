"""
OptiBuddy DSL Repository Dashboard
登録済みDSL定義・Extension・進化履歴を可視化するCLIダッシュボード
"""

import json

from repository import DslRepository


def print_header(title: str):
    """セクションヘッダーを表示"""
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def print_dsl_definitions(repo: DslRepository):
    """DSL定義一覧を表示"""
    print_header("📋 登録済みDSL定義")
    
    dsls = repo.list_dsl_definitions()
    if not dsls:
        print("  （登録なし）")
        return
    
    for dsl in dsls:
        ext_count = len(dsl["extensions"])
        ext_str = f" + {ext_count} extensions" if ext_count > 0 else ""
        
        print(f"\n  🔹 {dsl['problem_class']} v{dsl['version']}{ext_str}")
        print(f"     ID: {dsl['id']}")
        print(f"     説明: {dsl['description']}")
        
        if dsl["extensions"]:
            print(f"     Extensions: {', '.join(dsl['extensions'])}")
        
        print(f"     作成日時: {dsl['created_at']}")


def print_extensions(repo: DslRepository):
    """Extension一覧を表示"""
    print_header("🔧 登録済みExtensions")
    
    # カテゴリ別に整理
    categories = {}
    for ext in repo.list_extensions():
        cat = ext["category"] or "other"
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(ext)
    
    if not categories:
        print("  （登録なし）")
        return
    
    for category, exts in sorted(categories.items()):
        print(f"\n  📦 {category.upper()}")
        for ext in exts:
            print(f"     • {ext['name']}")
            print(f"       {ext['description']}")


def print_evolution_logs(repo: DslRepository, limit: int = 10):
    """進化ログを表示"""
    print_header(f"📈 DSL進化履歴（最新{limit}件）")
    
    # すべてのDSLの進化ログを取得
    all_logs = []
    for dsl in repo.list_dsl_definitions():
        logs = repo.get_evolution_history(dsl["id"])
        for log in logs:
            log["dsl_name"] = f"{dsl['problem_class']} v{dsl['version']}"
            all_logs.append(log)
    
    # 日時でソート
    all_logs.sort(key=lambda x: x["created_at"], reverse=True)
    
    if not all_logs:
        print("  （ログなし）")
        return
    
    for log in all_logs[:limit]:
        print(f"\n  📝 {log['created_at']}")
        print(f"     DSL: {log['dsl_name']}")
        print(f"     種別: {log['change_type']}")
        print(f"     内容: {log['change_description']}")


def print_dsl_detail(repo: DslRepository, problem_class: str, version: str):
    """DSL定義の詳細を表示"""
    print_header(f"🔍 DSL詳細: {problem_class} v{version}")
    
    dsl = repo.get_dsl_definition(problem_class, version)
    if not dsl:
        print(f"  ❌ DSL定義が見つかりません: {problem_class} v{version}")
        return
    
    print(f"\n  ID: {dsl['id']}")
    print(f"  説明: {dsl['description']}")
    print(f"  Extensions: {', '.join(dsl['extensions']) if dsl['extensions'] else 'なし'}")
    print(f"  作成日時: {dsl['created_at']}")
    print(f"  更新日時: {dsl['updated_at']}")
    
    print("\n  📄 スキーマ:")
    print(json.dumps(dsl["schema_json"], indent=2, ensure_ascii=False))
    
    # Extension詳細
    if dsl["extensions"]:
        print("\n  🔧 Extension詳細:")
        for ext_name in dsl["extensions"]:
            ext = repo.get_extension(ext_name)
            if ext:
                print(f"\n    • {ext_name} ({ext['category']})")
                print(f"      {ext['description']}")
                
                if ext["solver_mapping"]:
                    print(f"      ソルバーマッピング:")
                    constraints = ext['solver_mapping'].get('constraints', [])
                    if constraints:
                        print(f"        制約: {', '.join(constraints)}")
                    impl = ext['solver_mapping'].get('implementation', 'N/A')
                    print(f"        実装: {impl}")


def print_extension_detail(repo: DslRepository, name: str):
    """Extension詳細を表示"""
    print_header(f"🔍 Extension詳細: {name}")
    
    ext = repo.get_extension(name)
    if not ext:
        print(f"  ❌ Extensionが見つかりません: {name}")
        return
    
    print(f"\n  ID: {ext['id']}")
    print(f"  カテゴリ: {ext['category']}")
    print(f"  説明: {ext['description']}")
    print(f"  作成日時: {ext['created_at']}")
    print(f"  更新日時: {ext['updated_at']}")
    
    print("\n  📄 スキーマ追加:")
    print(json.dumps(ext["schema_fragment"], indent=2, ensure_ascii=False))
    
    if ext["solver_mapping"]:
        print("\n  🔧 ソルバーマッピング:")
        print(json.dumps(ext["solver_mapping"], indent=2, ensure_ascii=False))
    
    if ext["ui_mapping"]:
        print("\n  🎨 UIマッピング:")
        print(json.dumps(ext["ui_mapping"], indent=2, ensure_ascii=False))
    
    # このExtensionを使用しているDSL一覧
    print("\n  📋 使用しているDSL:")
    dsls = repo.list_dsl_definitions()
    using_dsls = [d for d in dsls if name in d["extensions"]]
    if using_dsls:
        for dsl in using_dsls:
            print(f"    • {dsl['problem_class']} v{dsl['version']}")
    else:
        print("    （なし）")


def print_overview(repo: DslRepository):
    """リポジトリ概要を表示"""
    print_header("📊 OptiBuddy DSL Repository 概要")
    
    overview = repo.get_overview()
    
    print(f"\n  DSL定義数:        {overview['dsl_definitions_count']} 件")
    print(f"  Extension数:      {overview['extensions_count']} 件")
    print(f"  進化ログ数:       {overview['evolution_logs_count']} 件")
    print(f"  Problem Classes:  {', '.join(overview['problem_classes'])}")


def interactive_menu(repo: DslRepository):
    """インタラクティブメニュー"""
    while True:
        print("\n" + "=" * 70)
        print("  OptiBuddy DSL Repository Dashboard")
        print("=" * 70)
        print("\n  [1] 概要表示")
        print("  [2] DSL定義一覧")
        print("  [3] Extension一覧")
        print("  [4] 進化ログ")
        print("  [5] DSL詳細表示")
        print("  [6] Extension詳細表示")
        print("  [0] 終了")
        
        choice = input("\n  選択してください: ").strip()
        
        if choice == "0":
            print("\n  👋 終了します")
            break
        elif choice == "1":
            print_overview(repo)
        elif choice == "2":
            print_dsl_definitions(repo)
        elif choice == "3":
            print_extensions(repo)
        elif choice == "4":
            print_evolution_logs(repo)
        elif choice == "5":
            problem_class = input("\n  Problem Class: ").strip()
            version = input("  Version: ").strip()
            print_dsl_detail(repo, problem_class, version)
        elif choice == "6":
            name = input("\n  Extension名: ").strip()
            print_extension_detail(repo, name)
        else:
            print("\n  ❌ 無効な選択です")
        
        input("\n  [Enter]キーで続行...")


def main():
    """メイン処理"""
    import sys
    
    repo = DslRepository()
    
    # コマンドライン引数で動作を切り替え
    if len(sys.argv) > 1:
        command = sys.argv[1]
        
        if command == "overview":
            print_overview(repo)
        elif command == "dsls":
            print_dsl_definitions(repo)
        elif command == "extensions":
            print_extensions(repo)
        elif command == "logs":
            print_evolution_logs(repo)
        elif command == "dsl" and len(sys.argv) >= 4:
            print_dsl_detail(repo, sys.argv[2], sys.argv[3])
        elif command == "ext" and len(sys.argv) >= 3:
            print_extension_detail(repo, sys.argv[2])
        elif command == "all":
            print_overview(repo)
            print_dsl_definitions(repo)
            print_extensions(repo)
            print_evolution_logs(repo)
        else:
            print("使用方法:")
            print("  python dashboard.py                    # インタラクティブモード")
            print("  python dashboard.py overview           # 概要表示")
            print("  python dashboard.py dsls               # DSL一覧")
            print("  python dashboard.py extensions         # Extension一覧")
            print("  python dashboard.py logs               # 進化ログ")
            print("  python dashboard.py dsl <class> <ver>  # DSL詳細")
            print("  python dashboard.py ext <name>         # Extension詳細")
            print("  python dashboard.py all                # すべて表示")
    else:
        # インタラクティブモード
        interactive_menu(repo)


if __name__ == "__main__":
    main()
