# サイト計画・進捗メモ

## 目的
- 各ブランチの `index.html` にアクセスできるトップページを GitHub Action で自動生成する。
- 階層ブランチを見やすく表示し、`description.md` があれば説明文も載せる。
- GitHub Pages 向けの静的サイトとして、ライトモード・モバイル表示を前提に整える。

## 設計
1. GitHub Actions で全ブランチを収集し、GitHub Pages 用の成果物を生成する。
2. Python スクリプトでブランチごとの静的ファイルを書き出し、トップページを生成する。
3. トップページにはブランチ一覧と更新履歴のみを掲載する。
4. 各ブランチでは `index.html` と任意の `description.md` を用意して内容を説明できるようにする。

## 進捗チェック
- [x] リポジトリ構成と既存のビルド・テスト手段を確認
- [x] GitHub Pages 向けの構成と生成フローを設計
- [x] ブランチ一覧トップページを生成する GitHub Action を追加
- [x] エージェント向け指示ファイルを追加
- [x] サンプルの `index.html` と `description.md` を追加
- [x] ローカル生成物の確認とスクリーンショット取得
- [x] 最終レビュー・セキュリティ確認・履歴更新

## 再開メモ
- 生成コマンド: `python3 scripts/generate_branch_pages.py --output-dir /tmp/learn-my-about-site --repository sutaa12/learn-my-about`
- 確認対象: `/tmp/learn-my-about-site/index.html` と `/tmp/learn-my-about-site/branches/.../index.html`
- 公開履歴は `CHANGELOG.md`、作業メモはこのファイルに集約する。
