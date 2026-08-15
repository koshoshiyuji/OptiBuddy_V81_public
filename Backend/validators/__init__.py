"""
Backend/validators

ドメイン固有のソルバー入力DSLバリデータ群。

【2026-07-13】NurseShift（週次夜勤上限なしの旧ベース版）の完全削除に伴い、
このパッケージが唯一持っていた試験実装 NurseShiftValidator を削除した。
現時点でドメイン固有バリデータの実装はない
（domain_generator.py の _DOMAIN_VALIDATORS も現在は空）。
新しいドメイン用バリデータを追加する場合はここに追加し、
domain_generator.py._register_default_domain_validators() にも登録すること。
"""

__all__: list = []
