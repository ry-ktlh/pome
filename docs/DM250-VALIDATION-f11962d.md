# pome PR #1 / f11962d — DM250検証手順

対象: `f11962d0b8b69c120b357d748b4b8ed2e2f9d413`（pome 1.1.2）。
この手順は実機で実行するためのものです。クラウドでの成功は実機合格を意味しません。

## 1. 実機の情報と隔離した導入

DM250の普段の日本語端末で実行します。同じシェル内で続けてください。
既存のエディタ・設定・原稿は変更しません。

```sh
uname -m
dpkg --print-architecture
python3 --version
locale charmap
git --version

POME_TEST_ROOT=$(mktemp -d "$HOME/pome-check.XXXXXX")
export POME_TEST_ROOT
git clone --branch pome-safety-hardening-20261103 --single-branch \
  https://github.com/ry-ktlh/pome.git "$POME_TEST_ROOT/src"
cd "$POME_TEST_ROOT/src"
git checkout --detach f11962d0b8b69c120b357d748b4b8ed2e2f9d413
test "$(git rev-parse HEAD)" = f11962d0b8b69c120b357d748b4b8ed2e2f9d413
```

ここまでにエラーがあれば停止し、出力を共有してください。

```sh
export POME_PREFIX="$POME_TEST_ROOT/install"
export XDG_CONFIG_HOME="$POME_TEST_ROOT/config"
export XDG_STATE_HOME="$POME_TEST_ROOT/state"
sh install.sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
mkdir -p "$POME_TEST_ROOT/manuscripts"
"$POME_PREFIX/bin/pome" "$POME_TEST_ROOT/manuscripts/日本語.txt"
```

合格: pome 1.1.2が起動し、33テストが成功する。git関連テストのスキップがあれば合格扱いにせずgitの有無を確認する。
この段階のGit統合テストはローカルbareリモートを使用するため通信不要。

## 2. 実際のキーボード・IME・画面

- 普段のIMEで「今日は日本語の執筆環境を確認します。」を変換・確定する。
- ひらがな、漢字、句読点、全角・半角混在、改行、画面幅を超える長い行を入力する。
- 矢印、Backspace、Delete、選択、Ctrl+Z/Ctrl+Yを試す。
- 入力停止から3秒以上待ち、「自動保存しました」を確認する。
- Ctrl+Qで終了し、同じ起動コマンドで再開する。

合格: 変換・確定・カーソル位置・折り返しが自然で、再開後の内容が入力どおり。
端末名（fbterm等）、IME名、画面の崩れや反応の遅さも記録する。

## 3. 誤操作からの復旧

テスト原稿のみで行う。

1. Ctrl+Aで全文選択し、別の文字を入力。自動保存まで待つ。
2. Ctrl+Zで元の文章に戻す。再び自動保存を待って終了・再開する。
3. 再び全文を置換して自動保存後に終了する。
4. 再開し、Esc → ファイル → 保存履歴から復元。
5. 一覧を閉じて番号を指定し、プレビューを確認して復元する。
6. Ctrl+Sで保存し、終了・再開する。

合格: 2と6で元の文章が戻り、履歴復元は取消可能。

緊急退避の補足: この版では `.pome-rescue-<hash>-<random>.txt` が原稿と同じ場所に作られる。
隠しファイルのためpomeの「開く」一覧には出ない。エラー時に表示された退避パスを指定して開く。
探す場合は `ls -la "$POME_TEST_ROOT/manuscripts"` で確認し、次のように個別指定する。

```sh
"$POME_PREFIX/bin/pome" "$POME_TEST_ROOT/manuscripts/.pome-rescue-実際の名前.txt"
```

異常終了を再現するために実原稿を開いた状態で強制終了・電源断しない。
退避ファイルは通常の保存履歴とは別であり、電源断やSIGKILLを保証するものではない。

## 4. 実ネットワークでのGit同期（専用テスト用リモート）

ローカルbareの成功とは別に、普段のWi-Fi／テザリングと認証を確認する。
実原稿のリポジトリは使用しない。空の専用テスト用リモートを用意し、そのURLを確認してから始める。
パスワードやトークンはチャットへ貼らない。

- 別の作業ディレクトリにテスト用リモートをcloneし、日本語のテスト原稿をpomeで編集・保存する。
- ツール → Git プッシュを実行し、PC側から取得して本文の一致を確認する。
- PC側でその原稿を編集・コミット・送信し、DM250側をクリーンにしてGit プルする。
- 受信した本文が表示されることを確認する。
- テスト用原稿の送信時に回線を切って失敗させる。復旧後に再送して成功することを確認する。
- 別ファイルをステージした場合は送信を拒否し、その変更を保持することを確認する。

Git pushは現在のブランチの未送信コミットも送る。独立した試験用リポジトリに限定する。
分岐した履歴の強制pushや自動マージは、この試験に含めない。

## 結果の返信用

- commit: f11962d0b8b69c120b357d748b4b8ed2e2f9d413
- 機種／OS／アーキテクチャ:
- Python／Git:
- 端末／IME／文字コード:
- 33テスト: 成功 / 失敗 / スキップ（出力添付）
- 日本語入力・画面表示:
- 自動保存→再開:
- Undoと履歴復元:
- ローカルGit試験:
- 実ネットワークの送信・受信・再送:
- エラー／気づいた点:

全項目確認まではDraft PRを維持する。試験データは結果確認が終わるまで残す。
