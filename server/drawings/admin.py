from django.contrib import admin

from . import models as m


@admin.register(m.SourceFile)
class SourceFileAdmin(admin.ModelAdmin):
    list_display = ("path", "kind", "status", "size", "mtime", "last_seen")
    list_filter = ("kind", "status")
    search_fields = ("path",)
    readonly_fields = ("sha256", "last_seen", "updated_at")


class BomInline(admin.TabularInline):
    model = m.BomItem
    extra = 0
    fields = ("row", "item_no", "part_no", "name", "qty", "material", "ref_drawing_no", "confidence", "verified")


@admin.register(m.Drawing)
class DrawingAdmin(admin.ModelAdmin):
    list_display = ("drawing_no", "revision", "title", "doc_type", "doc_type_fixed", "source", "confidence", "needs_ocr")
    list_filter = ("doc_type", "doc_type_fixed", "source", "needs_ocr")
    list_editable = ("doc_type",)  # 自動判定の誤りをここで直せる。直したものは「手で確定」になり、自動判定で戻らない

    def save_model(self, request, obj, form, change):
        if change and "doc_type" in form.changed_data:
            obj.doc_type_fixed = True
        super().save_model(request, obj, form, change)
    search_fields = ("drawing_no", "title", "file__path")
    inlines = [BomInline]
    exclude = ("search_text",)


@admin.register(m.BomItem)
class BomItemAdmin(admin.ModelAdmin):
    list_display = ("drawing", "row", "part_no", "name", "qty", "confidence", "verified")
    list_filter = ("verified",)
    list_editable = ("verified",)
    search_fields = ("part_no", "name", "drawing__drawing_no")


@admin.register(m.DictionaryEntry)
class DictionaryAdmin(admin.ModelAdmin):
    list_display = ("term", "canonical", "kind", "origin", "approved")
    list_filter = ("approved", "kind", "origin")
    list_editable = ("approved",)
    search_fields = ("term", "canonical")
    actions = ["approve"]

    @admin.action(description="選んだ語を承認する")
    def approve(self, request, queryset):
        queryset.update(approved=True)


@admin.register(m.Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ("id", "file", "stage", "state", "attempts", "worker", "updated_at")
    list_filter = ("state", "stage")
    actions = ["requeue"]

    @admin.action(description="選んだジョブをやり直す")
    def requeue(self, request, queryset):
        queryset.update(state=m.Job.State.QUEUED, attempts=0, error="")


admin.site.register([m.Relation, m.HouseFamily, m.Derivation, m.HouseSpec])


@admin.register(m.AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("at", "user", "action", "target")
    list_filter = ("action",)
    date_hierarchy = "at"
