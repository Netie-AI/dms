"""DOCX from laid-out boxes. Text is stored whole in one run per paragraph."""

from __future__ import annotations

from dms_core.create_style.layout import Box, Page
from dms_core.create_style.pack import alpha_val, emu, rgb, write_zip, xml_text

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"
_WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_WPS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"


def docx_bytes(pages: list[Page]) -> bytes:
    images: list[bytes] = []
    paragraphs: list[str] = []
    next_id = 1
    for index, page in enumerate(pages):
        runs: list[str] = []
        for box in page.boxes:
            if box.name == "paper":
                runs.append(_shape(box, next_id, behind=True))
            elif box.kind == "image" and box.image:
                images.append(box.image)
                runs.append(_picture(box, next_id, len(images)))
            elif box.kind == "rect":
                runs.append(_shape(box, next_id, behind=False))
            elif box.kind == "text" and box.text:
                runs.append(_textbox(box, next_id))
            else:
                continue
            next_id += 1
        paragraphs.append(f"<w:p><w:r>{''.join(runs)}</w:r></w:p>")
        if index != len(pages) - 1:
            paragraphs.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
    face = "Arial"
    east = "Noto Sans CJK SC"
    for page in pages:
        for box in page.boxes:
            if box.kind == "text" and box.font:
                face = box.font
                east = box.ea or box.office or face
                break
        else:
            continue
        break
    width = pages[0].w
    height = pages[0].h
    # 96 px per inch, 1440 twips per inch.
    twips_w = width * 15
    twips_h = height * 15
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W}" xmlns:r="{_R}" xmlns:wp="{_WP}" '
        f'xmlns:a="{_A}" xmlns:pic="{_PIC}" xmlns:wps="{_WPS}">'
        f"<w:body>{''.join(paragraphs)}"
        "<w:sectPr>"
        f'<w:pgSz w:w="{twips_w}" w:h="{twips_h}"/>'
        '<w:pgMar w:top="0" w:right="0" w:bottom="0" w:left="0" '
        'w:header="0" w:footer="0" w:gutter="0"/>'
        "</w:sectPr></w:body></w:document>"
    )
    rels = [
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
    ]
    for image_index in range(1, len(images) + 1):
        rels.append(
            f'<Relationship Id="rId{image_index + 1}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            f'Target="media/image{image_index}.png"/>'
        )
    parts: dict[str, bytes | str] = {
        "[Content_Types].xml": _content_types(len(images)),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" '
            'Target="word/document.xml"/>'
            "</Relationships>"
        ),
        "word/document.xml": document,
        "word/styles.xml": _styles(face, east),
        "word/_rels/document.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(rels)
            + "</Relationships>"
        ),
    }
    for image_index, raw in enumerate(images, start=1):
        parts[f"word/media/image{image_index}.png"] = raw
    return write_zip(parts)


def _anchor(box: Box, shape_id: int, inner: str, *, behind: bool) -> str:
    flag = "1" if behind else "0"
    return (
        '<w:drawing><wp:anchor distT="0" distB="0" distL="0" distR="0" '
        f'simplePos="0" relativeHeight="{shape_id}" behindDoc="{flag}" locked="0" '
        'layoutInCell="1" allowOverlap="1">'
        '<wp:simplePos x="0" y="0"/>'
        f'<wp:positionH relativeFrom="page"><wp:posOffset>{emu(box.x)}</wp:posOffset>'
        "</wp:positionH>"
        f'<wp:positionV relativeFrom="page"><wp:posOffset>{emu(box.y)}</wp:posOffset>'
        "</wp:positionV>"
        f'<wp:extent cx="{emu(box.w)}" cy="{emu(box.h)}"/>'
        '<wp:effectExtent l="0" t="0" r="0" b="0"/>'
        "<wp:wrapNone/>"
        f'<wp:docPr id="{shape_id}" name="{xml_text(box.name)}"/>'
        '<wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/></wp:cNvGraphicFramePr>'
        f"{inner}</wp:anchor></w:drawing>"
    )


def _picture(box: Box, shape_id: int, image_index: int) -> str:
    inner = (
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        "<pic:pic><pic:nvPicPr>"
        f'<pic:cNvPr id="{shape_id}" name="{xml_text(box.name)}"/>'
        '<pic:cNvPicPr><a:picLocks noChangeAspect="1"/></pic:cNvPicPr>'
        "</pic:nvPicPr><pic:blipFill>"
        f'<a:blip r:embed="rId{image_index + 1}"/>'
        "<a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr><a:xfrm>"
        f'<a:off x="0" y="0"/><a:ext cx="{emu(box.w)}" cy="{emu(box.h)}"/>'
        '</a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
        "</pic:pic></a:graphicData></a:graphic>"
    )
    return _anchor(box, shape_id, inner, behind=False)


def _fill(box: Box) -> str:
    colour = rgb(box.fill or "#FFFFFF")
    return (
        f'<a:solidFill><a:srgbClr val="{colour}">'
        f'<a:alpha val="{alpha_val(box.alpha)}"/></a:srgbClr></a:solidFill>'
    )


def _shape(box: Box, shape_id: int, *, behind: bool) -> str:
    inner = (
        '<a:graphic><a:graphicData uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
        "<wps:wsp><wps:cNvSpPr/><wps:spPr><a:xfrm>"
        f'<a:off x="0" y="0"/><a:ext cx="{emu(box.w)}" cy="{emu(box.h)}"/>'
        '</a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        f"{_fill(box)}</wps:spPr><wps:txbx><w:txbxContent><w:p/></w:txbxContent></wps:txbx>"
        '<wps:bodyPr wrap="square" lIns="0" tIns="0" rIns="0" bIns="0" anchor="t"/>'
        "</wps:wsp></a:graphicData></a:graphic>"
    )
    return _anchor(box, shape_id, inner, behind=behind)


def _textbox(box: Box, shape_id: int) -> str:
    half = max(box.size * 3 // 2, 2)
    line = box.line * 15
    bold = "<w:b/>" if box.bold else ""
    italic = "<w:i/>" if box.italic else ""
    east = xml_text(box.ea or box.office or box.font)
    paragraphs = []
    for part in box.text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        paragraphs.append(
            "<w:p><w:pPr>"
            f'<w:spacing w:before="0" w:after="0" w:line="{line}" w:lineRule="exact"/>'
            "</w:pPr><w:r><w:rPr>"
            f'<w:rFonts w:ascii="{xml_text(box.font)}" w:hAnsi="{xml_text(box.font)}" '
            f'w:eastAsia="{east}" w:cs="{east}"/>'
            f'{bold}{italic}<w:sz w:val="{half}"/><w:szCs w:val="{half}"/>'
            f'<w:color w:val="{rgb(box.color)}"/>'
            "</w:rPr>"
            f'<w:t xml:space="preserve">{xml_text(part)}</w:t>'
            "</w:r></w:p>"
        )
    inner = (
        '<a:graphic><a:graphicData uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
        '<wps:wsp><wps:cNvSpPr txBox="1"/><wps:spPr><a:xfrm>'
        f'<a:off x="0" y="0"/><a:ext cx="{emu(box.w)}" cy="{emu(box.h)}"/>'
        '</a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        "<a:noFill/></wps:spPr><wps:txbx><w:txbxContent>"
        f"{''.join(paragraphs)}</w:txbxContent></wps:txbx>"
        '<wps:bodyPr wrap="square" lIns="0" tIns="0" rIns="0" bIns="0" anchor="t"/>'
        "</wps:wsp></a:graphicData></a:graphic>"
    )
    return _anchor(box, shape_id, inner, behind=False)


def _content_types(image_count: int) -> str:
    extra = ""
    if image_count:
        extra = '<Default Extension="png" ContentType="image/png"/>'
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f"{extra}"
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        "</Types>"
    )


def _styles(face: str, east: str) -> str:
    latin = xml_text(face)
    cjk = xml_text(east)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
        '<w:name w:val="Normal"/>'
        f'<w:rPr><w:rFonts w:ascii="{latin}" w:hAnsi="{latin}" '
        f'w:eastAsia="{cjk}" w:cs="{cjk}"/><w:sz w:val="22"/></w:rPr>'
        "</w:style></w:styles>"
    )
