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
terra-hatch https://github.com/azat/chdig --category apps   # フォルダ選び
terra-hatch owner/repo --nightly                            # commit 追跡
terra-hatch owner/repo --dry-run                            # 書く前に見る
```

## 内容

- 言語判定(`go.mod` / `Cargo.toml` / `pyproject.toml` / その他)
- 事実を自動で入れる: 最新 version、license(SPX)、説明、あなたの
  packager 設定、`update.rhai` の追跡(`crates()` / `pypi()` 優先)
- わからない所は `TODO` で残し、最後に確認シートを出す

出力はスタートのテンプレート。TODO を全部埋めて
[Terra packaging policies](https://docs.terrapkg.com/contributing/policies)
に従ってから build と PR をしてください。

## Build

```sh
anda build -c terra-rawhide-x86_64 anda/<category>/<name>/pkg
```

新規 scratch フォルダには root の `anda.hcl` が要ります。`--init-root`
で作れます。

## 新言語

言語の設定は `src/terra_hatch/languages/` の中で、言語ごとに 1 ファイルです。
新言語の追加は:

1. `languages/<なまえ>.py` を作る(`ROW` と `sniff()` があればよい)
2. `languages/__init__.py` のテーブル(`LANGUAGES` / `SNIFFERS`)に登録
3. `templates/` に `.spec.j2` を置く
