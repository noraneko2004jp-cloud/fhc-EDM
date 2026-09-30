"""DWG-FIND のデータモデル（設計書 8 章）。すべて Ubuntu の PostgreSQL に置く。"""
from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.db import models
from pgvector.django import VectorField


class SourceFile(models.Model):
    """ファイルサーバー上の 1 ファイル。巡回で見つかったものをすべて持つ。"""

    class Kind(models.TextChoices):
        DXF = "dxf", "DXF"
        PDF = "pdf", "PDF"

    class Status(models.TextChoices):
        PENDING = "pending", "解析待ち"
        DONE = "done", "解析済み"
        ERROR = "error", "エラー"
        MISSING = "missing", "見つからない"

    path = models.CharField("パス", max_length=1024, unique=True, help_text="共有フォルダからの相対パス（/ 区切り）")
    kind = models.CharField("種類", max_length=8, choices=Kind.choices)
    size = models.BigIntegerField("サイズ")
    mtime = models.DateTimeField("更新日時")
    sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    status = models.CharField("状態", max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    last_seen = models.DateTimeField("最終確認", null=True, blank=True)
    error = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = verbose_name_plural = "ファイル"

    def __str__(self):
        return self.path

    @property
    def filename(self):
        return self.path.rsplit("/", 1)[-1]


class Drawing(models.Model):
    """1 ファイル（図面一式の PDF はそのうちの 1 ページ）から読み取った図面情報。"""

    class Source(models.TextChoices):
        ATTRIB = "attrib", "DXF属性"
        TEXT = "text", "PDFテキスト層"
        OCR = "ocr", "OCR"
        FILENAME = "filename", "ファイル名"

    file = models.ForeignKey(SourceFile, on_delete=models.CASCADE, related_name="drawings")
    page_no = models.PositiveIntegerField("ページ", default=1, help_text="図面一式の PDF で、この図面が載っているページ")
    drawing_no = models.CharField("図番", max_length=64, blank=True, db_index=True)
    revision = models.CharField("改訂", max_length=16, blank=True)
    title = models.CharField("品名", max_length=255, blank=True)
    material = models.CharField("材質", max_length=128, blank=True)
    scale = models.CharField("尺度", max_length=32, blank=True)
    drawn_date = models.CharField("作成日", max_length=32, blank=True)
    source = models.CharField("読み取り元", max_length=16, choices=Source.choices, default=Source.TEXT)
    confidence = models.FloatField("信頼度", default=1.0)
    needs_ocr = models.BooleanField("OCR待ち", default=False)
    attributes = models.JSONField("その他の属性", default=dict, blank=True)
    thumbnail = models.CharField(max_length=255, blank=True, help_text="MEDIA_ROOT からの相対パス")
    # 検索用：図番・品名・材質・部品表・パスを正規化して連結したもの（pg_trgm で部分一致検索）
    search_text = models.TextField(blank=True)
    doc_type = models.CharField("文書種別", max_length=16, default="other", db_index=True,
                                choices=[("drawing", "製品図面"), ("site", "敷地・土地図"), ("contract", "契約書・見積"),
                                         ("application", "申請書類"), ("other", "その他")],
                                help_text="classify.py で自動判定。ゲストは「製品図面」だけ見られる")
    doc_type_fixed = models.BooleanField("種別を手で確定", default=False,
                                         help_text="管理画面で種別を直すと付く。付いていると自動判定で上書きしない")
    # グループ化用（classify.py で決める。"_" は不明）
    model_family = models.CharField("型式系統", max_length=16, blank=True, db_index=True)
    model_code = models.CharField("型式", max_length=64, blank=True, db_index=True)
    series_prefix = models.CharField("図番の頭", max_length=16, blank=True, db_index=True)
    series = models.CharField("図番の系列", max_length=64, blank=True, db_index=True)
    parsed_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = verbose_name_plural = "図面"
        ordering = ["drawing_no", "revision"]
        constraints = [models.UniqueConstraint(fields=["file", "page_no"], name="drawing_file_page_unique")]
        # search_text は保存時に NFKC＋大文字へ正規化済み。LIKE '%語%' を trigram 索引で高速化する
        indexes = [GinIndex(fields=["search_text"], opclasses=["gin_trgm_ops"], name="drawing_search_trgm")]

    def __str__(self):
        return f"{self.drawing_no or self.file.filename} Rev.{self.revision or '-'}"


class Page(models.Model):
    drawing = models.ForeignKey(Drawing, on_delete=models.CASCADE, related_name="pages")
    page_no = models.PositiveIntegerField(default=1)
    text = models.TextField(blank=True)
    text_source = models.CharField(max_length=16, choices=Drawing.Source.choices, default=Drawing.Source.TEXT)
    embedding = VectorField(dimensions=1024, null=True, blank=True)  # bge-m3

    class Meta:
        unique_together = [("drawing", "page_no")]
        ordering = ["page_no"]


class BomItem(models.Model):
    """図面内の部品表の 1 行。"""

    drawing = models.ForeignKey(Drawing, on_delete=models.CASCADE, related_name="bom_items")
    row = models.PositiveIntegerField("行")
    item_no = models.CharField("No.", max_length=16, blank=True)
    part_no = models.CharField("品番", max_length=64, blank=True, db_index=True)
    name = models.CharField("品名", max_length=255, blank=True)
    qty = models.DecimalField("数量", max_digits=10, decimal_places=2, null=True, blank=True)
    material = models.CharField("材質", max_length=128, blank=True)
    ref_drawing_no = models.CharField("図番", max_length=64, blank=True, db_index=True)
    raw_text = models.TextField(blank=True)
    confidence = models.FloatField("信頼度", default=1.0)
    verified = models.BooleanField("確認済み", default=False)

    class Meta:
        verbose_name = verbose_name_plural = "部品表の行"
        ordering = ["drawing", "row"]


class Relation(models.Model):
    """図面どうしの関連（設計書 5 章）。"""

    class Kind(models.TextChoices):
        BOM_REF = "bom_ref", "部品表の図番"
        NOTE_REF = "note_ref", "参照図の記載"
        DERIVED = "derived", "派生元・派生"
        REVISION = "revision", "改訂系列"
        COMMON_PART = "common_part", "共通部品"
        SERIES = "series", "図番の系列"
        SIMILAR = "similar", "内容の類似"

    from_drawing = models.ForeignKey(Drawing, on_delete=models.CASCADE, related_name="relations_out")
    to_drawing = models.ForeignKey(Drawing, on_delete=models.CASCADE, related_name="relations_in")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    weight = models.FloatField(default=0.5)
    evidence = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = verbose_name_plural = "関連"
        unique_together = [("from_drawing", "to_drawing", "kind")]


class HouseFamily(models.Model):
    """派生系統（基本ハウスとその派生の束）。設計書 6 章。"""

    base_drawing_no = models.CharField("基本図番", max_length=64, unique=True)
    model_name = models.CharField("型式", max_length=64, blank=True)
    width = models.PositiveIntegerField("間口", null=True, blank=True)
    depth = models.PositiveIntegerField("奥行", null=True, blank=True)

    class Meta:
        verbose_name = verbose_name_plural = "派生系統"

    def __str__(self):
        return self.base_drawing_no


class Derivation(models.Model):
    class Via(models.TextChoices):
        BRANCH = "branch_no", "図番の枝番・接尾辞"
        NOTE = "note", "表題欄・注記"
        PATH = "path", "フォルダ・ファイル名"
        MANUAL = "manual", "手動"

    family = models.ForeignKey(HouseFamily, on_delete=models.CASCADE, related_name="derivations")
    base_drawing = models.ForeignKey(Drawing, on_delete=models.CASCADE, related_name="derived_from_me")
    derived_drawing = models.ForeignKey(Drawing, on_delete=models.CASCADE, related_name="derivations")
    via = models.CharField(max_length=16, choices=Via.choices)
    evidence = models.CharField(max_length=255, blank=True)
    confidence = models.FloatField(default=1.0)
    verified = models.BooleanField(default=False)

    class Meta:
        verbose_name = verbose_name_plural = "派生"
        unique_together = [("base_drawing", "derived_drawing")]


class HouseSpec(models.Model):
    drawing = models.OneToOneField(Drawing, on_delete=models.CASCADE, related_name="spec")
    usage = models.CharField("用途", max_length=64, blank=True)
    windows = models.PositiveIntegerField("窓", default=0)
    doors = models.PositiveIntegerField("ドア", default=0)
    shutters = models.PositiveIntegerField("シャッター", default=0)
    partitions = models.PositiveIntegerField("間仕切り", default=0)
    options = models.JSONField("仕様・オプション", default=list, blank=True)

    class Meta:
        verbose_name = verbose_name_plural = "ハウス仕様"


class DictionaryEntry(models.Model):
    """同義語・表記ゆれ・OCR補正の辞書。承認済みのものだけ検索に使う。"""

    class Kind(models.TextChoices):
        SYNONYM = "synonym", "同義語"
        OCR_FIX = "ocr_fix", "OCR補正"
        ABBR = "abbr", "略称"

    class Origin(models.TextChoices):
        RULE = "rule", "ルール"
        LLM = "llm", "Ollama提案"
        HUMAN = "human", "人が登録"

    term = models.CharField("語", max_length=128, db_index=True)
    canonical = models.CharField("代表語", max_length=128, db_index=True)
    kind = models.CharField("種類", max_length=16, choices=Kind.choices, default=Kind.SYNONYM)
    origin = models.CharField("出どころ", max_length=16, choices=Origin.choices, default=Origin.HUMAN)
    approved = models.BooleanField("承認", default=False)

    class Meta:
        verbose_name = verbose_name_plural = "辞書"
        unique_together = [("term", "canonical")]

    def __str__(self):
        return f"{self.term} → {self.canonical}"


class Job(models.Model):
    """Mac mini のワーカーに渡す解析の仕事。"""

    class Stage(models.TextChoices):
        PARSE = "parse", "解析"
        OCR = "ocr", "OCR"
        LLM = "llm", "LLM構造化"
        EMBED = "embed", "ベクトル化"

    class State(models.TextChoices):
        QUEUED = "queued", "待ち"
        RUNNING = "running", "実行中"
        DONE = "done", "完了"
        FAILED = "failed", "失敗"

    file = models.ForeignKey(SourceFile, on_delete=models.CASCADE, related_name="jobs")
    stage = models.CharField(max_length=16, choices=Stage.choices, default=Stage.PARSE)
    state = models.CharField(max_length=16, choices=State.choices, default=State.QUEUED, db_index=True)
    priority = models.IntegerField(default=100, help_text="小さいほど先に処理")
    attempts = models.PositiveIntegerField(default=0)
    worker = models.CharField(max_length=64, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = verbose_name_plural = "ジョブ"
        indexes = [models.Index(fields=["state", "priority", "id"])]


class AuditLog(models.Model):
    class Action(models.TextChoices):
        VIEW = "view", "閲覧"
        DOWNLOAD = "download", "ダウンロード"
        EXPORT = "export", "出力"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=16, choices=Action.choices)
    target = models.CharField(max_length=1024)
    at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = verbose_name_plural = "監査ログ"
        ordering = ["-at"]
