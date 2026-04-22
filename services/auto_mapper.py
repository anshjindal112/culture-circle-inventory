"""
Auto-mapper: maps SourceX product names to blank types.

Strategy:
1. KEYWORD EXTRACTION (primary): Parse product name to extract garment type + base color,
   then find matching blank in blank_master.
2. IMAGE MATCHING (fallback): If keywords don't yield a confident match,
   scrape the product image from Culture Circle and compare visually.
3. MANUAL (last resort): Flag for human review.
"""

import re
import requests
import hashlib
from urllib.parse import quote_plus

# --- Garment type keywords, ordered longest-first to avoid partial matches ---
GARMENT_KEYWORDS = [
    # Outerwear
    ('Quarter Zip Tshirt', ['quarter-zip jumper', 'quarter zip jumper', 'quarter-zip sweatshirt',
                            'quarter zip sweatshirt', 'quarter-zip', 'quater zip', 'quarter zip',
                            'polo quater zip']),
    ('Vegan Leather Jacket', ['vegan leather jacket']),
    ('Bomber Jacket', ['bomber jacket']),
    ('Hooded Jacket', ['hooded jacket']),
    ('Zipper Hoodie', ['zipped oversized hoodie', 'zipper hoodie']),
    ('Hoodie', ['hoodie', 'hooded']),
    ('Polo Sweatshirt', ['polo sweatshirt', 'polo sweatshirts duo']),
    ('Sweatshirt', ['sweatshirt']),
    ('Cable Sweater', ['cable sweater', 'zipped cable sweater']),
    # Polos (before generic shirt/tee)
    ('Signature House Polo', ['signature house polo']),
    ('Legacy Classic Zip Polo', ['legacy classic zip polo']),
    ('Monaco Knit Polo', ['monaco knit polo']),
    # Tops (T-shirts MUST come before generic Shirt)
    ('Waffle Shirt', ['waffle t-shirt', 'waffle tee', 'waffle shirt']),
    ('Acid Wash T-Shirt', ['acid wash oversized t-shirt', 'acid wash oversized hoodie',
                           'acid wash oversized']),
    ('Oversized T-Shirt', ['oversized t-shirt', 'oversized tee', 'oversized tshirt']),
    ('Oversized T-Shirt', ['t-shirt', 'tshirt', 't shirt']),
    # Shirts (after T-shirts)
    ('Bowling Shirt', ['bowling shirt']),
    # Forfksake print shirts are white bowling shirt blanks
    ('Bowling Shirt', ['forfksake locally hated shirt', 'forfksake parental advisory shirt',
                        'forfksake unbutton me shirt', 'forfksake last night policy shirt',
                        'forfksake nude authority shirt', 'forfksake warning label shirt',
                        'forfksake self muse shirt', 'forfksake drunk dial shirt',
                        'forfksake shift badge shirt', 'forfksake whatever you moron shirt',
                        'forfksake hiring shirt', 'forfksake walkout shirt',
                        'forfksake drunk excuse shirt', 'forfksake i am fashion shirt',
                        'forfksake late shirt']),
    ('Stripped Shirt', ['striped shirt', 'stripe shirt', 'stripped shirt']),
    ('Capri Linen Summer Shirt', ['capri linen summer shirt', 'capri linen']),
    ('Linen Shirt', ['linen shirt']),
    ('Solid Cotton Full Sleeve Shirt', ['solid cotton full sleeve', 'mercer shirt', 'airweave shirt',
                                         'camp collar shirt', 'cotton shirt']),
    ('Solid Cotton Full Sleeve Shirt', ['henley shirt', 'piped henley shirt']),
    ('Shirt', ['shirt']),
    ('Compression T-Shirt', ['compression t-shirt']),
    ('Long Sleeve', ['long sleeve']),
    ('Tank Top (Square Neck)', ['tank', 'rib tank', 'rib beige tank', 'rib gray tank',
                                 'rib brown tank']),
    ('Top', ['top', 'halter neck']),
    # Bottoms
    ('Denims', ['denims', 'denim', 'jeans']),
    ('Linen Pant', ['linen pants', 'linen pant']),
    ('Trousers', ['trousers']),
    ('Jogger', ['jogger']),
    ('Shorts', ['shorts', 'jorts']),
    ('Cargo', ['cargo']),
    ('Pant', ['pant', 'pants']),
    ('Flared', ['flared soft touch']),
    ('Corset Top', ['corset top']),
]

# --- Color keywords ---
# Ordered longest-first; values must match blank_master.color exactly
COLOR_KEYWORDS = [
    # Treatments first (longer matches)
    ('Acid Wash Black', ['acid wash black', 'acid wash oversized t-shirt black',
                         'acid wash oversized hoodie black']),
    ('Acid Wash Navy', ['acid wash navy', 'acid wash oversized hoodie navy']),
    ('Acid Wash Blue', ['acid wash blue', 'blue acid wash']),
    ('Acid Wash', ['acid wash']),
    # Specific shades (must come before generic colors)
    ('DARK BROWN', ['dark brown', 'dark cocoa']),
    ('LIGHT BROWN', ['light brown']),
    ('DARK GREEN', ['dark green', 'forest green', 'forest']),
    ('FOREST GREEN', ['forest green', 'forest']),
    ('OLIVE GREEN', ['olive green', 'olive']),
    ('NAVY BLUE', ['navy blue', 'deep navy', 'midnight navy', 'marine blue', 'ink blue']),
    ('SKY BLUE', ['sky blue']),
    ('ICE BLUE', ['ice blue']),
    ('LIGHT BLUE', ['light blue', 'horizon blue']),
    ('BABY PINK', ['baby pink', 'blush', 'rose']),
    ('LIGHT PINK', ['light pink']),
    # Standard colors
    ('BLACK', ['black', 'noir', 'obsidian', 'jet black', 'midnight']),
    ('WHITE', ['white', 'ivory', 'off white', 'off-white', 'cloud', 'glacier']),
    ('CREAM', ['cream', 'oat']),
    ('NAVY', ['navy']),
    ('RED', ['red', 'crimson']),
    ('WINE', ['wine', 'maroon', 'plum']),
    ('PINK', ['pink']),
    ('BLUE', ['blue', 'teal', 'teel']),
    ('GREEN', ['green', 'sage green', 'eucalyptus', 'moss']),
    ('BROWN', ['brown', 'cocoa', 'chestnut', 'espresso', 'earth clay']),
    ('BEIGE', ['beige', 'sand', 'oatmeal', 'desert sand', 'warm oat', 'butter', 'dune']),
    ('GREY', ['gray', 'grey', 'ash', 'heather gray']),
    ('PURPLE', ['purple']),
    ('YELLOW', ['yellow']),
    ('ORANGE', ['orange']),
    ('BURGUNDY', ['burgundy', 'burgandy']),
]


# Words that need word-boundary matching to avoid false positives
_WORD_BOUNDARY_KEYWORDS = {'tee': 'Oversized T-Shirt'}


def extract_garment_type(product_name):
    """Extract the garment type from a product name using keyword matching."""
    name_lower = product_name.lower()

    # First pass: standard keyword list
    for garment_type, keywords in GARMENT_KEYWORDS:
        for kw in keywords:
            if kw in name_lower:
                return garment_type

    # Second pass: word-boundary keywords (like 'tee' which could match 'street')
    for word, garment_type in _WORD_BOUNDARY_KEYWORDS.items():
        if re.search(r'\b' + re.escape(word) + r'\b', name_lower):
            return garment_type

    return None


def extract_color(product_name):
    """Extract the base color from a product name."""
    name_lower = product_name.lower()

    # Forfksake print shirts without color in name are WHITE bowling shirt blanks
    _white_shirt_keywords = [
        'forfksake locally hated', 'forfksake parental advisory', 'forfksake unbutton me',
        'forfksake last night policy', 'forfksake nude authority', 'forfksake warning label',
        'forfksake self muse', 'forfksake drunk dial', 'forfksake shift badge',
        'forfksake whatever you moron', 'forfksake hiring', 'forfksake walkout',
        'forfksake drunk excuse', 'forfksake i am fashion', 'forfksake late shirt',
    ]
    for kw in _white_shirt_keywords:
        if kw in name_lower:
            return 'WHITE'

    # Gymbrat Solid / Kaand Staple are black oversized tees
    if 'gymbrat solid everyday' in name_lower or 'kaand studio staple' in name_lower:
        return 'BLACK'

    for color, keywords in COLOR_KEYWORDS:
        for kw in keywords:
            if kw in name_lower:
                return color
    return None


def auto_map_product(product_name, size, blanks_cache=None):
    """
    Attempt to auto-map a product name + size to a blank.

    Args:
        product_name: Full product name from SourceX CSV
        size: Size from CSV
        blanks_cache: List of dicts from blank_master (optional, for performance)

    Returns:
        dict with keys:
            'blank_id': int or None
            'confidence': 'high', 'medium', 'low', 'none'
            'garment_type': extracted garment type
            'color': extracted color
            'method': 'keyword', 'image', 'none'
    """
    garment_type = extract_garment_type(product_name)
    color = extract_color(product_name)

    result = {
        'blank_id': None,
        'confidence': 'none',
        'garment_type': garment_type,
        'color': color,
        'method': 'none',
        'suggested_blank_name': None,
    }

    if not garment_type:
        return result

    # If we have both garment type and color, high confidence
    if garment_type and color:
        result['confidence'] = 'high'
        result['method'] = 'keyword'
        result['suggested_blank_name'] = f"{color} {garment_type}"
    elif garment_type:
        result['confidence'] = 'medium'
        result['method'] = 'keyword'
        result['suggested_blank_name'] = garment_type

    return result


def find_matching_blank(product_name, size, db_query_fn):
    """
    Find a matching blank in the database for a product + size.

    Args:
        product_name: Product name from CSV
        size: Size from CSV
        db_query_fn: Function to run DB queries (e.g., db.database.query)

    Returns:
        blank_id (int) or None
    """
    # Step 1: Check existing mapping first
    mapping = db_query_fn(
        "SELECT blank_id FROM sku_blank_mapping WHERE product = %s AND size = %s",
        (product_name, size), fetch='one'
    )
    if mapping:
        return mapping['blank_id']

    # Step 2: Try keyword auto-mapping
    info = auto_map_product(product_name, size)

    if info['confidence'] in ('high', 'medium') and info['suggested_blank_name']:
        # Try to find blank by garment_type + color + size
        if info['color']:
            blank = db_query_fn(
                """SELECT blank_id FROM blank_master
                   WHERE LOWER(garment_type) = LOWER(%s) AND LOWER(color) = LOWER(%s) AND size = %s AND is_active = TRUE""",
                (info['garment_type'], info['color'], size), fetch='one'
            )
            if blank:
                return blank['blank_id']

        # Try by garment_type + size only (color might be stored differently)
        blank = db_query_fn(
            """SELECT blank_id FROM blank_master
               WHERE LOWER(garment_type) = LOWER(%s) AND size = %s AND is_active = TRUE
               LIMIT 1""",
            (info['garment_type'], size), fetch='one'
        )
        if blank:
            return blank['blank_id']

        # Try fuzzy: blank_name ILIKE the suggested name
        blank = db_query_fn(
            """SELECT blank_id FROM blank_master
               WHERE LOWER(blank_name) ILIKE %s AND size = %s AND is_active = TRUE
               LIMIT 1""",
            (f"%{info['suggested_blank_name'].lower()}%", size), fetch='one'
        )
        if blank:
            return blank['blank_id']

    return None


def auto_map_and_save(product_name, brand, size, db_query_fn, db_execute_fn):
    """
    Try to auto-map a product to a blank and save the mapping if found.

    Returns:
        blank_id (int) or None
    """
    blank_id = find_matching_blank(product_name, size, db_query_fn)

    if blank_id:
        # Save the mapping for future lookups
        try:
            db_execute_fn(
                """INSERT INTO sku_blank_mapping (product, brand, size, blank_id)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (product, size) DO NOTHING""",
                (product_name, brand, size, blank_id)
            )
        except Exception:
            pass  # Mapping might already exist
        return blank_id

    return None


def get_image_url_from_culture_circle(product_name):
    """
    Fallback: Try to find the product image on Culture Circle.
    Returns the image URL or None.

    This is the image-based fallback when keyword extraction doesn't have
    enough info to determine the blank type.
    """
    try:
        search_url = f"https://culturecircle.in/search?q={quote_plus(product_name)}"
        resp = requests.get(search_url, timeout=10, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        })
        if resp.status_code != 200:
            return None

        # Simple regex to find product image (og:image or first product image)
        match = re.search(r'<meta property="og:image" content="([^"]+)"', resp.text)
        if match:
            return match.group(1)

        # Try finding img tags with product images
        img_match = re.search(r'<img[^>]+src="(https://[^"]+(?:product|cdn)[^"]*)"', resp.text)
        if img_match:
            return img_match.group(1)

    except Exception:
        pass

    return None


def suggest_blank_from_image(product_name, size, db_query_fn):
    """
    Image-based fallback: Get product image and try to determine blank type
    by analyzing the image URL/filename for clues.

    This is a lightweight version — for full image matching,
    you'd use a vision API.

    Returns:
        dict with suggestion info or None
    """
    image_url = get_image_url_from_culture_circle(product_name)
    if not image_url:
        return None

    return {
        'image_url': image_url,
        'product_name': product_name,
        'size': size,
        'suggestion': 'Manual review needed — image available for reference',
    }
