"""Generación y lectura de plantillas XLSX sin dependencias externas"""
from io import BytesIO
import posixpath
import re
import unicodedata
import zipfile
import xml.etree.ElementTree as ET

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
ET.register_namespace("", MAIN_NS)
ET.register_namespace("r", REL_NS)


def _columna(n):
    resultado = ""
    while n:
        n, resto = divmod(n - 1, 26)
        resultado = chr(65 + resto) + resultado
    return resultado


def _xml_bytes(elemento):
    return ET.tostring(elemento, encoding="utf-8", xml_declaration=True)


def crear_plantilla(titulo, instrucciones, encabezados, filas, anchos, columnas_entrada=(), formulas=None):
    """Devuelve un XLSX con título, instrucciones, filtros, panel congelado y celdas de entrada resaltadas."""
    formulas = formulas or {}
    total_cols = len(encabezados)
    ultima = _columna(total_cols)
    sheet = ET.Element(f"{{{MAIN_NS}}}worksheet")
    ET.SubElement(sheet, f"{{{MAIN_NS}}}dimension", {"ref": f"A1:{ultima}{max(4 + len(filas), 4)}"})
    views = ET.SubElement(sheet, f"{{{MAIN_NS}}}sheetViews")
    view = ET.SubElement(views, f"{{{MAIN_NS}}}sheetView", {"workbookViewId": "0"})
    ET.SubElement(view, f"{{{MAIN_NS}}}pane", {"ySplit": "4", "topLeftCell": "A5", "activePane": "bottomLeft", "state": "frozen"})
    cols = ET.SubElement(sheet, f"{{{MAIN_NS}}}cols")
    for i, ancho in enumerate(anchos, 1):
        ET.SubElement(cols, f"{{{MAIN_NS}}}col", {"min": str(i), "max": str(i), "width": str(ancho), "customWidth": "1"})
    data = ET.SubElement(sheet, f"{{{MAIN_NS}}}sheetData")

    def cell(row, col, value=None, style="5", formula=None):
        ref = f"{_columna(col)}{row}"
        attrs = {"r": ref, "s": style}
        if formula is None and isinstance(value, (int, float)):
            node = ET.SubElement(current_row, f"{{{MAIN_NS}}}c", attrs)
            ET.SubElement(node, f"{{{MAIN_NS}}}v").text = str(value)
        elif formula is not None:
            node = ET.SubElement(current_row, f"{{{MAIN_NS}}}c", attrs)
            ET.SubElement(node, f"{{{MAIN_NS}}}f").text = formula
            ET.SubElement(node, f"{{{MAIN_NS}}}v")
        else:
            attrs["t"] = "inlineStr"
            node = ET.SubElement(current_row, f"{{{MAIN_NS}}}c", attrs)
            inline = ET.SubElement(node, f"{{{MAIN_NS}}}is")
            text = ET.SubElement(inline, f"{{{MAIN_NS}}}t")
            text.text = "" if value is None else str(value)
            if text.text and (text.text != text.text.strip()):
                text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")

    current_row = ET.SubElement(data, f"{{{MAIN_NS}}}row", {"r": "1", "ht": "29", "customHeight": "1"})
    cell(1, 1, titulo, "2")
    current_row = ET.SubElement(data, f"{{{MAIN_NS}}}row", {"r": "2", "ht": "54", "customHeight": "1"})
    cell(2, 1, instrucciones, "3")
    ET.SubElement(data, f"{{{MAIN_NS}}}row", {"r": "3", "ht": "8", "customHeight": "1"})
    current_row = ET.SubElement(data, f"{{{MAIN_NS}}}row", {"r": "4", "ht": "26", "customHeight": "1"})
    for i, header in enumerate(encabezados, 1):
        cell(4, i, header, "1")

    for row_idx, valores in enumerate(filas, 5):
        current_row = ET.SubElement(data, f"{{{MAIN_NS}}}row", {"r": str(row_idx), "ht": "21", "customHeight": "1"})
        for col_idx, valor in enumerate(valores, 1):
            if col_idx in formulas:
                formula = formulas[col_idx].format(row=row_idx)
                cell(row_idx, col_idx, style="5", formula=formula)
            else:
                style = "4" if col_idx in columnas_entrada else "5"
                cell(row_idx, col_idx, valor, style)
    ET.SubElement(sheet, f"{{{MAIN_NS}}}autoFilter", {"ref": f"A4:{ultima}{max(4 + len(filas), 4)}"})
    merge_cells = ET.SubElement(sheet, f"{{{MAIN_NS}}}mergeCells", {"count": "1"})
    ET.SubElement(merge_cells, f"{{{MAIN_NS}}}mergeCell", {"ref": f"A2:{ultima}2"})

    styles = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="{MAIN_NS}">
<fonts count="4"><font><sz val="11"/><name val="Aptos"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Aptos"/></font><font><b/><color rgb="FF17324D"/><sz val="16"/><name val="Aptos Display"/></font><font><color rgb="FF526579"/><sz val="10"/><name val="Aptos"/></font></fonts>
<fills count="5"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF17324D"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFF1F5F9"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFFFF4CC"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border><border><left style="thin"><color rgb="FFE2E8F0"/></left><right style="thin"><color rgb="FFE2E8F0"/></right><top style="thin"><color rgb="FFE2E8F0"/></top><bottom style="thin"><color rgb="FFE2E8F0"/></bottom><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="6"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="center" horizontal="center" wrapText="1"/></xf><xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/><xf numFmtId="0" fontId="3" fillId="3" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="0" fillId="4" borderId="1" xfId="0" applyFill="1" applyBorder="1"/><xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1"/></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'''
    workbook = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="{MAIN_NS}" xmlns:r="{REL_NS}"><bookViews><workbookView workbookViewId="0"/></bookViews><sheets><sheet name="{titulo[:31]}" sheetId="1" r:id="rId1"/></sheets><calcPr calcMode="auto"/></workbook>'''
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>'''
    root_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'''
    workbook_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>'''
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as book:
        book.writestr("[Content_Types].xml", content_types)
        book.writestr("_rels/.rels", root_rels)
        book.writestr("xl/workbook.xml", workbook)
        book.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        book.writestr("xl/styles.xml", styles)
        book.writestr("xl/worksheets/sheet1.xml", _xml_bytes(sheet))
    return buffer.getvalue()


def _xlsx_rows(file_obj):
    try:
        with zipfile.ZipFile(file_obj) as archive:
            shared = []
            if "xl/sharedStrings.xml" in archive.namelist():
                root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
                shared = ["".join(node.itertext()) for node in root.findall(f"{{{MAIN_NS}}}si")]
            sheet_name = "xl/worksheets/sheet1.xml"
            if "xl/workbook.xml" in archive.namelist() and "xl/_rels/workbook.xml.rels" in archive.namelist():
                workbook = ET.fromstring(archive.read("xl/workbook.xml"))
                sheet = workbook.find(f"{{{MAIN_NS}}}sheets/{{{MAIN_NS}}}sheet")
                if sheet is not None:
                    rid = sheet.attrib.get(f"{{{REL_NS}}}id")
                    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
                    rel = next((r for r in rels.findall(f"{{{PKG_REL_NS}}}Relationship") if r.attrib.get("Id") == rid), None)
                    if rel is not None:
                        target = rel.attrib["Target"].lstrip("/")
                        sheet_name = posixpath.normpath(target if target.startswith("xl/") else posixpath.join("xl", target))
            root = ET.fromstring(archive.read(sheet_name))
    except (zipfile.BadZipFile, KeyError, ET.ParseError, StopIteration) as exc:
        raise ValueError("El archivo Excel no es válido o está dañado.") from exc
    rows = []
    for row in root.findall(f"{{{MAIN_NS}}}sheetData/{{{MAIN_NS}}}row"):
        result = {}
        for cell in row.findall(f"{{{MAIN_NS}}}c"):
            ref = cell.attrib.get("r", "")
            letters = re.match(r"[A-Z]+", ref)
            if not letters:
                continue
            index = 0
            for char in letters.group():
                index = index * 26 + ord(char) - 64
            kind = cell.attrib.get("t")
            if kind == "inlineStr":
                value = "".join(cell.find(f"{{{MAIN_NS}}}is").itertext()) if cell.find(f"{{{MAIN_NS}}}is") is not None else ""
            else:
                node = cell.find(f"{{{MAIN_NS}}}v")
                value = node.text if node is not None and node.text is not None else ""
                if kind == "s" and value:
                    value = shared[int(value)]
            result[index] = value
        if result:
            rows.append([result.get(i, "") for i in range(1, max(result) + 1)])
        else:
            rows.append([])
    return rows


def _normalizar(texto):
    texto = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode().lower()
    texto = re.sub(r"[^a-z0-9]+", " ", texto).strip()
    aliases = {
        "alicuota porcentaje": "alicuota", "porcentaje alicuota": "alicuota",
        "medidor": "tipo", "lectura anterior": "anterior", "lectura actual": "actual",
        "tarifa por unidad": "tarifa", "tarifa unidad": "tarifa",
    }
    return aliases.get(texto, texto.replace(" ", "_"))


def leer_tabla(archivo, requeridas):
    nombre = getattr(archivo, "name", "").lower()
    if nombre.endswith(".xlsx"):
        filas = _xlsx_rows(archivo.file)
    else:
        import csv
        import io
        contenido = archivo.read().decode("utf-8-sig")
        filas = list(csv.reader(io.StringIO(contenido)))
    requeridas = set(requeridas)
    indice = None
    headers = None
    for pos, fila in enumerate(filas):
        posibles = [_normalizar(v) for v in fila]
        if requeridas.issubset(set(posibles)):
            indice, headers = pos, posibles
            break
    if indice is None:
        raise ValueError("No encontré una fila de encabezados compatible con esta plantilla.")
    resultado = []
    for fila in filas[indice + 1:]:
        resultado.append({headers[i]: (fila[i] if i < len(fila) else "") for i in range(len(headers))})
    return resultado
