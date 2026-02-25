import re
from typing import Dict


def parse_tags(str_with_tags: str) -> Dict[str, str]:
    """Parse tags in format <tag_name>...</tag_name> and return dict"""
    tags = re.findall(r"<(.*?)>(.*?)</\1>", str_with_tags, re.DOTALL)
    return {tag: content.strip() for tag, content in tags}
