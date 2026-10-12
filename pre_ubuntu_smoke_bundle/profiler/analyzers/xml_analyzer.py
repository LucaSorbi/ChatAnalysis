"""
profiler/analyzers/xml_analyzer.py
Analyses XML/UFDR files: tag frequency, depth, attributes.
"""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
import xml.etree.ElementTree as ET
from profiler.config import ProfilerConfig


@dataclass
class XMLProfile:
    file_path: str
    size_bytes: int
    root_tag: str
    max_depth: int
    tag_frequency: dict
    attribute_frequency: dict
    total_elements: int
    errors: list = field(default_factory=list)


def _walk(elem, depth=0, tags=None, attrs=None, max_d=None):
    if tags is None: tags = Counter(); attrs = Counter(); max_d = [0]
    tags[elem.tag] += 1
    if depth > max_d[0]: max_d[0] = depth
    for k in elem.attrib: attrs[k] += 1
    for child in elem: _walk(child, depth+1, tags, attrs, max_d)
    return tags, attrs, max_d

def analyze(xml_path: Path, cfg: ProfilerConfig) -> XMLProfile:
    root_dir = Path(cfg.source_path).resolve()
    rel = str(xml_path.relative_to(root_dir)) if xml_path.is_absolute() else str(xml_path)
    size = xml_path.stat().st_size
    errors = []; root_tag = "unknown"; max_depth = 0; tag_freq = {}; attr_freq = {}; total = 0
    try:
        tree = ET.parse(str(xml_path))
        root = tree.getroot()
        root_tag = root.tag
        tags, attrs, max_d = _walk(root)
        max_depth = max_d[0]; total = sum(tags.values())
        tag_freq = dict(tags.most_common(30)); attr_freq = dict(attrs.most_common(20))
    except ET.ParseError as e: errors.append(f"ParseError: {e}")
    except Exception as e:     errors.append(str(e))
    return XMLProfile(file_path=rel, size_bytes=size, root_tag=root_tag,
                      max_depth=max_depth, tag_frequency=tag_freq,
                      attribute_frequency=attr_freq, total_elements=total,
                      errors=errors)
