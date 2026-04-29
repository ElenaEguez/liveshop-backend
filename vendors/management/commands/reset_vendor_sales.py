from pathlib import Path
from datetime import datetime

from django.apps import apps
from django.conf import settings
from django.core import serializers
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from vendors.models import Vendor, TurnoCaja


class Command(BaseCommand):
    help = (
        "Respalda y limpia ventas/pagos del vendor (POS, web y live) "
        "sin tocar productos."
    )

    def add_arguments(self, parser):
        parser.add_argument("--vendor-slug", required=True, help="Slug del vendor a limpiar.")
        parser.add_argument(
            "--output-dir",
            default="backups/sales-resets",
            help="Directorio para el backup JSON.",
        )
        parser.add_argument(
            "--include-expenses",
            action="store_true",
            help="Incluye gastos operativos en la limpieza.",
        )
        parser.add_argument(
            "--execute",
            action="store_true",
            help="Ejecuta la limpieza. Sin esta bandera solo hace dry-run + backup.",
        )

    def _model(self, app_label, model_name):
        return apps.get_model(app_label, model_name)

    def handle(self, *args, **options):
        slug = (options["vendor_slug"] or "").strip()
        if not slug:
            raise CommandError("Debe enviar --vendor-slug.")

        try:
            vendor = Vendor.objects.get(slug=slug)
        except Vendor.DoesNotExist as exc:
            raise CommandError(f"No existe vendor con slug '{slug}'.") from exc

        backup_models = [
            ("payments", "Payment"),
            ("payments", "PagoCredito"),
            ("payments", "VentaPOSItem"),
            ("payments", "VentaPOS"),
            ("orders", "Reservation"),
            ("website_builder", "CartOrderItem"),
            ("website_builder", "CartOrder"),
            ("vendors", "MovimientoCaja"),
            ("vendors", "TurnoCaja"),
            ("vendors", "KardexMovimiento"),
        ]
        if options["include_expenses"]:
            backup_models.append(("payments", "GastoOperativo"))

        filter_by_vendor = {}
        filter_by_vendor["payments.Payment"] = {"reservation__session__vendor": vendor}
        filter_by_vendor["payments.PagoCredito"] = {"venta__vendor": vendor}
        filter_by_vendor["payments.VentaPOSItem"] = {"venta__vendor": vendor}
        filter_by_vendor["payments.VentaPOS"] = {"vendor": vendor}
        filter_by_vendor["orders.Reservation"] = {"session__vendor": vendor}
        filter_by_vendor["website_builder.CartOrderItem"] = {"order__vendor": vendor}
        filter_by_vendor["website_builder.CartOrder"] = {"vendor": vendor}
        filter_by_vendor["vendors.MovimientoCaja"] = {"turno__caja__sucursal__vendor": vendor}
        filter_by_vendor["vendors.TurnoCaja"] = {"caja__sucursal__vendor": vendor}
        filter_by_vendor["vendors.KardexMovimiento"] = {"inventory__product__vendor": vendor, "motivo__in": ["venta", "venta_live"]}
        filter_by_vendor["payments.GastoOperativo"] = {"vendor": vendor}

        now = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = Path(settings.BASE_DIR) / options["output_dir"]
        output_dir.mkdir(parents=True, exist_ok=True)
        backup_path = output_dir / f"{slug}_sales_backup_{now}.json"

        counts = {}
        rows_to_backup = []
        for app_label, model_name in backup_models:
            model = self._model(app_label, model_name)
            key = f"{app_label}.{model_name}"
            qs = model.objects.filter(**filter_by_vendor[key]).order_by("pk")
            counts[key] = qs.count()
            rows_to_backup.extend(list(qs))

        with backup_path.open("w", encoding="utf-8") as fh:
            serializers.serialize("json", rows_to_backup, indent=2, stream=fh)

        self.stdout.write(self.style.SUCCESS(f"Backup generado: {backup_path}"))
        for key, value in counts.items():
            self.stdout.write(f" - {key}: {value}")

        if not options["execute"]:
            self.stdout.write(self.style.WARNING("Dry-run: no se eliminó ningún dato. Use --execute para limpiar."))
            return

        with transaction.atomic():
            # Orden pensado para evitar referencias huérfanas durante la transacción.
            self._model("payments", "Payment").objects.filter(reservation__session__vendor=vendor).delete()
            self._model("payments", "PagoCredito").objects.filter(venta__vendor=vendor).delete()
            self._model("payments", "VentaPOSItem").objects.filter(venta__vendor=vendor).delete()
            self._model("payments", "VentaPOS").objects.filter(vendor=vendor).delete()
            self._model("website_builder", "CartOrderItem").objects.filter(order__vendor=vendor).delete()
            self._model("website_builder", "CartOrder").objects.filter(vendor=vendor).delete()
            self._model("orders", "Reservation").objects.filter(session__vendor=vendor).delete()
            self._model("vendors", "MovimientoCaja").objects.filter(turno__caja__sucursal__vendor=vendor).delete()
            TurnoCaja.objects.filter(caja__sucursal__vendor=vendor).delete()
            self._model("vendors", "KardexMovimiento").objects.filter(
                inventory__product__vendor=vendor,
                motivo__in=["venta", "venta_live"],
            ).delete()
            if options["include_expenses"]:
                self._model("payments", "GastoOperativo").objects.filter(vendor=vendor).delete()

        self.stdout.write(self.style.SUCCESS(f"Limpieza completada para vendor '{slug}'."))
