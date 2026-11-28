"""PPTX from laid-out boxes. One slide per page. Text is stored whole."""

from __future__ import annotations

from dms_core.create_style.layout import Box, Page
from dms_core.create_style.pack import alpha_val, emu, rgb, write_zip, xml_text

_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"


def pptx_bytes(pages: list[Page]) -> bytes:
    images: list[bytes] = []
    parts: dict[str, bytes | str] = {}
    slide_rels_ids: list[list[tuple[str, str]]] = []
    for page_index, page in enumerate(pages, start=1):
        local: list[tuple[str, str]] = []
        shapes: list[str] = []
        shape_id = 2
        for box in page.boxes:
            if box.name == "paper":
                shapes.append(_shape(box, shape_id))
            elif box.kind == "rect":
                shapes.append(_shape(box, shape_id))
            elif box.kind == "image" and box.image:
                images.append(box.image)
                rid = f"rId{len(local) + 2}"
                local.append((rid, f"../media/image{len(images)}.png"))
                shapes.append(_picture(box, shape_id, rid))
            elif box.kind == "text" and box.text:
                shapes.append(_text(box, shape_id))
            else:
                continue
            shape_id += 1
        slide_rels_ids.append(local)
        parts[f"ppt/slides/slide{page_index}.xml"] = _slide("".join(shapes))
        rels = [_rel("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml")]
        for rid, target in local:
            rels.append(_rel(rid, "image", target))
        parts[f"ppt/slides/_rels/slide{page_index}.xml.rels"] = _rels(rels)
    width = emu(pages[0].w)
    height = emu(pages[0].h)
    sld_ids = []
    pres_rels = [_rel("rId1", "slideMaster", "slideMasters/slideMaster1.xml")]
    for index in range(1, len(pages) + 1):
        rid = f"rId{index + 1}"
        sld_ids.append(f'<p:sldId id="{255 + index}" r:id="{rid}"/>')
        pres_rels.append(_rel(rid, "slide", f"slides/slide{index}.xml"))
    parts["ppt/presentation.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<p:presentation xmlns:a="{_A}" xmlns:r="{_R}" xmlns:p="{_P}">'
        '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
        f"<p:sldIdLst>{''.join(sld_ids)}</p:sldIdLst>"
        f'<p:sldSz cx="{width}" cy="{height}" type="custom"/>'
        '<p:notesSz cx="6858000" cy="9144000"/>'
        "</p:presentation>"
    )
    parts["ppt/_rels/presentation.xml.rels"] = _rels(pres_rels)
    parts["ppt/slideMasters/slideMaster1.xml"] = _MASTER
    parts["ppt/slideMasters/_rels/slideMaster1.xml.rels"] = _rels(
        [
            _rel("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml"),
            _rel("rId2", "theme", "../theme/theme1.xml"),
        ]
    )
    parts["ppt/slideLayouts/slideLayout1.xml"] = _LAYOUT
    parts["ppt/slideLayouts/_rels/slideLayout1.xml.rels"] = _rels(
        [_rel("rId1", "slideMaster", "../slideMasters/slideMaster1.xml")]
    )
    parts["ppt/theme/theme1.xml"] = _THEME
    parts["docProps/core.xml"] = _CORE
    parts["docProps/app.xml"] = _APP
    parts["[Content_Types].xml"] = _content_types(len(pages), bool(images))
    parts["_rels/.rels"] = _rels(
        [
            _rel("rId1", "officeDocument", "ppt/presentation.xml"),
            _rel("rId2", "metadata/core-properties", "docProps/core.xml", package=True),
            _rel("rId3", "extended-properties", "docProps/app.xml"),
        ]
    )
    for index, raw in enumerate(images, start=1):
        parts[f"ppt/media/image{index}.png"] = raw
    return write_zip(parts)


def _rel(rid: str, kind: str, target: str, *, package: bool = False) -> str:
    base = _PKG if package else _R
    return f'<Relationship Id="{rid}" Type="{base}/{kind}" Target="{target}"/>'


def _rels(items: list[str]) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(items)
        + "</Relationships>"
    )


def _slide(shapes: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<p:sld xmlns:a="{_A}" xmlns:r="{_R}" xmlns:p="{_P}">'
        "<p:cSld><p:spTree>"
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
        '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        f"{shapes}</p:spTree></p:cSld>"
        "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>"
    )


def _xfrm(box: Box) -> str:
    return (
        "<a:xfrm>"
        f'<a:off x="{emu(box.x)}" y="{emu(box.y)}"/>'
        f'<a:ext cx="{emu(box.w)}" cy="{emu(box.h)}"/>'
        "</a:xfrm>"
    )


def _fill(box: Box) -> str:
    colour = rgb(box.fill or "#FFFFFF")
    return (
        f'<a:solidFill><a:srgbClr val="{colour}">'
        f'<a:alpha val="{alpha_val(box.alpha)}"/></a:srgbClr></a:solidFill>'
    )


def _shape(box: Box, shape_id: int) -> str:
    line = ""
    if box.name == "card":
        line = '<a:ln w="6350"><a:solidFill><a:srgbClr val="D9D4CB"/></a:solidFill></a:ln>'
    return (
        "<p:sp><p:nvSpPr>"
        f'<p:cNvPr id="{shape_id}" name="{xml_text(box.name)}"/>'
        "<p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr>"
        f'{_xfrm(box)}<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        f"{_fill(box)}{line}</p:spPr>"
        "<p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>"
    )


def _picture(box: Box, shape_id: int, rid: str) -> str:
    return (
        "<p:pic><p:nvPicPr>"
        f'<p:cNvPr id="{shape_id}" name="{xml_text(box.name)}"/>'
        '<p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/>'
        "</p:nvPicPr><p:blipFill>"
        f'<a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch>'
        f"</p:blipFill><p:spPr>{_xfrm(box)}"
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>'
    )


def _text(box: Box, shape_id: int) -> str:
    size = box.size * 75
    spacing = box.line * 75
    bold = ' b="1"' if box.bold else ""
    paragraphs = []
    for part in box.text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        paragraphs.append(
            "<a:p><a:pPr>"
            f'<a:lnSpc><a:spcPts val="{spacing}"/></a:lnSpc>'
            "</a:pPr><a:r><a:rPr"
            f' lang="en-US" sz="{size}"{bold} dirty="0">'
            f'<a:solidFill><a:srgbClr val="{rgb(box.color)}"/></a:solidFill>'
            f'<a:latin typeface="{xml_text(box.font)}"/></a:rPr>'
            f"<a:t>{xml_text(part)}</a:t></a:r></a:p>"
        )
    return (
        "<p:sp><p:nvSpPr>"
        f'<p:cNvPr id="{shape_id}" name="{xml_text(box.name)}"/>'
        '<p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr>'
        f'{_xfrm(box)}<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/>'
        "</p:spPr><p:txBody>"
        '<a:bodyPr wrap="square" lIns="0" tIns="0" rIns="0" bIns="0" anchor="t"/>'
        f"<a:lstStyle/>{''.join(paragraphs)}</p:txBody></p:sp>"
    )


def _content_types(pages: int, has_png: bool) -> str:
    overrides = [
        '<Override PartName="/ppt/presentation.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>',
        '<Override PartName="/ppt/slideMasters/slideMaster1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>',
        '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>',
        '<Override PartName="/ppt/theme/theme1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>',
        '<Override PartName="/docProps/core.xml" '
        'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>',
        '<Override PartName="/docProps/app.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>',
    ]
    for index in range(1, pages + 1):
        overrides.append(
            f'<Override PartName="/ppt/slides/slide{index}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
        )
    png = '<Default Extension="png" ContentType="image/png"/>' if has_png else ""
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f"{png}{''.join(overrides)}</Types>"
    )


_CORE = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/'
    'metadata/core-properties" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/">'
    "<dc:title>create</dc:title><dc:creator>dms</dc:creator>"
    "</cp:coreProperties>"
)

_APP = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
    "<Application>dms</Application></Properties>"
)

_MASTER = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<p:sldMaster xmlns:a="{_A}" xmlns:r="{_R}" xmlns:p="{_P}">'
    "<p:cSld><p:spTree>"
    '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
    '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
    '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
    "</p:spTree></p:cSld>"
    '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" '
    'accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" '
    'accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
    '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
    "<p:txStyles><p:titleStyle/><p:bodyStyle/><p:otherStyle/></p:txStyles>"
    "</p:sldMaster>"
)

_LAYOUT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<p:sldLayout xmlns:a="{_A}" xmlns:r="{_R}" xmlns:p="{_P}" type="blank" preserve="1">'
    '<p:cSld name="Blank"><p:spTree>'
    '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
    '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
    '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
    "</p:spTree></p:cSld>"
    "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>"
)

_THEME = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<a:theme xmlns:a="{_A}" name="pitch">'
    "<a:themeElements>"
    '<a:clrScheme name="pitch">'
    '<a:dk1><a:srgbClr val="1C1C1C"/></a:dk1>'
    '<a:lt1><a:srgbClr val="FFFFFF"/></a:lt1>'
    '<a:dk2><a:srgbClr val="5E6670"/></a:dk2>'
    '<a:lt2><a:srgbClr val="F4F1EA"/></a:lt2>'
    '<a:accent1><a:srgbClr val="1F4B3A"/></a:accent1>'
    '<a:accent2><a:srgbClr val="1F4B3A"/></a:accent2>'
    '<a:accent3><a:srgbClr val="1F4B3A"/></a:accent3>'
    '<a:accent4><a:srgbClr val="1F4B3A"/></a:accent4>'
    '<a:accent5><a:srgbClr val="1F4B3A"/></a:accent5>'
    '<a:accent6><a:srgbClr val="1F4B3A"/></a:accent6>'
    '<a:hlink><a:srgbClr val="1F4B3A"/></a:hlink>'
    '<a:folHlink><a:srgbClr val="5E6670"/></a:folHlink>'
    "</a:clrScheme>"
    '<a:fontScheme name="pitch">'
    '<a:majorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>'
    '<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont>'
    "</a:fontScheme>"
    '<a:fmtScheme name="pitch">'
    '<a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst>'
    "<a:lnStyleLst><a:ln w=\"6350\">"
    '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst>'
    "<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>"
    '<a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst>'
    "</a:fmtScheme></a:themeElements></a:theme>"
)
