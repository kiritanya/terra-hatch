# terra-hatch

Terra のあたらしいパッケージを 1 コマンドで用意するツールです。
英語版: [README.md](README.md)

```
terra-hatch https://github.com/hashicorp/terraform-ls
```

で `anda.hcl` と `<name>.spec` と `update.rhai` をまとめて書き出します。

## Install

```sh
pip install .
```

## Use

```sh
cd <your packages repo>   # anda/ の category フォルダがある場所
terra-hatch https://github.com/azat/chdig                     # フォルダは実行時に対話で選ぶ
echo apps | terra-hatch owner/repo                            # 対話をパイプで代用もできる
terra-hatch owner/repo --nightly                              # commit 追跡
terra-hatch owner/repo --dry-run                              # 書く前に見る
terra-hatch owner/repo --no-spec-tools                        # pyp2spec / go2rpm を使わない
```

フォルダの選択肢は `--root` (規定 `anda`) の下を実際に走査して集めます。
なので packages repo の種類 (terrapkg/packages でも tuatara でも) に
よらず、手元のフォルダ構成がそのまま出ます。`--root` の下に category
フォルダがないときは実行を止めます。

## 内容

- 言語判定(`go.mod` / `Cargo.toml` / `pyproject.toml` / その他)
- 事実を自動で入れる: 最新 version、license(SPX)、説明、あなたの
  packager 設定、`update.rhai` の追跡(`crates()` / `pypi()` 優先)
- わからない所は `TODO` で残し、最後に確認シートを出す

python の場合、spec は既定で Fedora の
[pyp2spec](https://pypi.org/project/pyp2spec/) で作られます
(入っていたとき、`dnf install pyp2spec`)。ただし PyPI の entry が
同じ upstream リポジトリに紐づくときだけ使うので、同名の別作品を
追いかけてしまうことはありません。使えないとき、または
`--no-spec-tools` を付けたときは内蔵テンプレートが使われます。
go の場合も同じ仕組みで [go2rpm](https://pagure.io/go2rpm) を
既定として使います。

## Build

```sh
anda build -c terra-rawhide-x86_64 anda/<category>/<name>/pkg
```

新規 scratch フォルダには root の `anda.hcl` が要ります。`--init-root`
で作れます。

## 新言語

言語の設定は `terra_hatch/languages/` の中で、言語ごとに 1 ファイルです。
新言語の追加は:

1. `languages/<なまえ>.py` を作る(`ROW` と `sniff()` があればよい)
2. `languages/__init__.py` のテーブル(`LANGUAGES` / `SNIFFERS`)に登録
3. `templates/` に `.spec.j2` を置く
