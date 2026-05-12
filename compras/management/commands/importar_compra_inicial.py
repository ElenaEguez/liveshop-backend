import csv
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from compras.models import OrdenCompra, OrdenCompraItem, Proveedor
from products.models import Product, ProductVariant
from vendors.models import Almacen, Vendor


def dec(value, default="0"):
    if value is None or str(value).strip() == "":
        return Decimal(default)
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return Decimal(default)


def as_int(value, default=0):
    if value is None or str(value).strip() == "":
        return default
    try:
        return int(Decimal(str(value).strip()))
    except (InvalidOperation, ValueError):
        return default


class Command(BaseCommand):
    help = "Crea una orden historica de compra inicial desde un CSV sin modificar inventario."

    def add_arguments(self, parser):
        parser.add_argument("csv_path", help="Ruta al CSV compras_migracion_preview.csv")
        parser.add_argument("--vendor-id", type=int, help="ID del vendor/tienda")
        parser.add_argument("--numero", default=f"MIG{date.today():%Y%m%d}", help="Numero unico de la orden")
        parser.add_argument("--proveedor", default="Carga inicial", help="Nombre del proveedor historico")
        parser.add_argument("--apply", action="store_true", help="Guarda cambios reales. Sin esto solo valida.")

    def handle(self, *args, **options):
        csv_path = options["csv_path"]
        apply = options["apply"]
        vendor = self._get_vendor(options.get("vendor_id"))
        numero = options["numero"]

        if OrdenCompra.objects.filter(numero=numero).exists():
            raise CommandError(f"Ya existe una orden con numero {numero}. Usa --numero otro valor.")

        rows = self._read_rows(csv_path)
        rows = [r for r in rows if (r.get("incluir") or "").strip().upper() == "SI"]
        if not rows:
            raise CommandError("No hay filas con incluir=SI.")

        summary = self._validate_rows(rows, vendor)
        self.stdout.write(self.style.WARNING("Modo: APPLY" if apply else "Modo: DRY RUN"))
        self.stdout.write(f"Vendor: {vendor.id} - {vendor.nombre_tienda}")
        self.stdout.write(f"Orden: {numero}")
        self.stdout.write(f"Items validos: {summary['items']}")
        self.stdout.write(f"Cantidad total historica: {summary['cantidad']}")
        self.stdout.write(f"Total historico: {summary['total']}")

        if not apply:
            self.stdout.write(self.style.SUCCESS("Validacion OK. Ejecuta de nuevo agregando --apply para guardar."))
            return

        with transaction.atomic():
            proveedor, _ = Proveedor.objects.get_or_create(
                vendor=vendor,
                nombre=options["proveedor"],
                defaults={"activo": True, "notas": "Proveedor generado para migracion inicial."},
            )
            orden = OrdenCompra.objects.create(
                vendor=vendor,
                proveedor=proveedor,
                numero=numero,
                factura_compra="MIGRACION-INICIAL",
                fecha=date.today(),
                estado="recibida",
                notas=(
                    "Compra historica creada por migracion. "
                    "No modifica stock porque el inventario ya estaba cargado."
                ),
                created_by=vendor.user,
            )

            for row in rows:
                product = Product.objects.get(id=as_int(row["producto_id"]), vendor=vendor)
                variant = None
                if (row.get("variante_id") or "").strip():
                    variant = ProductVariant.objects.get(id=as_int(row["variante_id"]), product=product)

                almacen = None
                if (row.get("almacen_id") or "").strip():
                    almacen = Almacen.objects.get(id=as_int(row["almacen_id"]), sucursal__vendor=vendor)

                OrdenCompraItem.objects.create(
                    orden=orden,
                    producto=product,
                    variante=variant,
                    almacen=almacen,
                    descripcion=self._description(row),
                    cantidad=as_int(row["cantidad"], 1),
                    costo_mercaderia=dec(row.get("costo_mercaderia_migracion")),
                    flete_unitario=dec(row.get("flete_migracion")),
                    porcentaje_ganancia=dec(row.get("porcentaje_ganancia_migracion")),
                    precio_unitario=dec(row.get("precio_unitario_migracion")),
                )
            orden.recalcular_totales()

        self.stdout.write(self.style.SUCCESS(f"Orden historica creada: {numero}. Stock no fue modificado."))

    def _get_vendor(self, vendor_id):
        if vendor_id:
            try:
                return Vendor.objects.get(id=vendor_id)
            except Vendor.DoesNotExist as exc:
                raise CommandError(f"No existe vendor id={vendor_id}") from exc
        vendors = list(Vendor.objects.all()[:2])
        if len(vendors) == 1:
            return vendors[0]
        raise CommandError("Hay mas de un vendor. Ejecuta con --vendor-id ID.")

    def _read_rows(self, csv_path):
        try:
            with open(csv_path, newline="", encoding="utf-8-sig") as fh:
                return list(csv.DictReader(fh))
        except FileNotFoundError as exc:
            raise CommandError(f"No existe el CSV: {csv_path}") from exc

    def _validate_rows(self, rows, vendor):
        total = Decimal("0")
        cantidad = 0
        for index, row in enumerate(rows, start=2):
            product_id = as_int(row.get("producto_id"))
            if not Product.objects.filter(id=product_id, vendor=vendor).exists():
                raise CommandError(f"Fila {index}: producto {product_id} no existe para vendor {vendor.id}.")

            variant_id = as_int(row.get("variante_id")) if row.get("variante_id") else None
            if variant_id and not ProductVariant.objects.filter(id=variant_id, product_id=product_id).exists():
                raise CommandError(f"Fila {index}: variante {variant_id} no pertenece al producto {product_id}.")

            almacen_id = as_int(row.get("almacen_id")) if row.get("almacen_id") else None
            if almacen_id and not Almacen.objects.filter(id=almacen_id, sucursal__vendor=vendor).exists():
                raise CommandError(f"Fila {index}: almacen {almacen_id} no pertenece al vendor {vendor.id}.")

            qty = as_int(row.get("cantidad"))
            if qty <= 0:
                raise CommandError(f"Fila {index}: cantidad debe ser mayor a cero.")
            cantidad += qty
            total += dec(row.get("precio_unitario_migracion")) * qty
        return {"items": len(rows), "cantidad": cantidad, "total": total}

    def _description(self, row):
        pieces = ["Migracion inicial"]
        if row.get("codigo_interno"):
            pieces.append(f"Codigo: {row['codigo_interno']}")
        if row.get("precio_venta_actual"):
            pieces.append(f"Precio original: {row['precio_venta_actual']}")
        if row.get("observaciones"):
            pieces.append(row["observaciones"])
        return " | ".join(pieces)[:300]
