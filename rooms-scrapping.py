import datetime
import os
import re
import requests
from bs4 import BeautifulSoup
import time
import json
from dotenv import load_dotenv

base_url = ""
max_price = 350

headers = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:139.0) Gecko/20100101 Firefox/139.0',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Accept-Encoding': 'gzip, deflate, br, zstd',
    'Connection': 'keep-alive',
}

cookies = {
    # TODO This cant be hardcoded since was some kind of TTL, need to find a way to generate this
    'datadome': 'mnkSP~m2Kh2o6hdzUQbcXKBoY2Jj_gzddAdsiERl~VJmTQlKMZubREmDUJNMCCbbnSQhAkh5H7h01ZNygw3UyFQxh03cHvuB4B8gN8ydjbR9Kd86hkv1qP8L4O~~t2C1'
}

def get_all_rooms():
    rooms_search_url = f"{base_url}/arrendar-quarto/braga/guimaraes/centro-da-cidade/com-preco-max_{max_price},genero_rapariga/"
    
    response = make_http_request(rooms_search_url)
    if response is not None:
        soup = BeautifulSoup(response.text, 'html.parser')
        all_rooms = extract_all_rooms_info(soup)
        return all_rooms
    else:
        print(f"Empty request on rooms search")
        return None
    
def extract_all_rooms_info(soup: BeautifulSoup):
    rooms = []
    try:
        articles_container = soup.select_one(".listing-items .items-container")
        articles = articles_container.select(".item")
        
        for article in articles:
            title_container = article.select_one(".item-link")
            title = title_container.getText().replace("\n","").strip()
            link = title_container.attrs.get("href")
            
            id = ""
            idMatch = re.search(r'/(\d+)/', link)
            if idMatch:
                id = idMatch.group(1)
            
            price = article.select_one(".item-price").getText().replace("\n","").strip()
            
            rooms.append({
                "id": id,
                "title": title,
                "link": link,
                "price": price
            })
        
        return rooms    
    except Exception as e:
        print("Error while extracing all rooms info", e)
        return None

def get_room_details(room_id):
    room_url = f"{base_url}/imovel/{room_id}/"
    
    response = make_http_request(room_url)
    if response is not None:
        soup = BeautifulSoup(response.text, 'html.parser')
        room_info = extract_room_info(soup)
        return room_info
    else:
        print(f"Empty request on room details for id: {room_id}")
        return None
    
def extract_room_info(soup: BeautifulSoup):
    room_info = {}
    try:
        utag_data_script = soup.find("script", string=lambda t: t and "utag_data" in t)
        if utag_data_script:
            script_content = utag_data_script.string
            json_str_match = re.search(r"var\s+utag_data\s*=\s*(\{.*?\});", script_content, re.DOTALL | re.IGNORECASE)
            if json_str_match:
                json_str = json_str_match.group(1)
                # Clean up common JS-valid but JSON-invalid constructs like trailing commas
                json_str = re.sub(r',\s*([}\]])', r'\1', json_str) # Remove trailing commas before } or ]
                json_str = re.sub(r';\s*$', '', json_str) # Remove trailing semicolon if any
                try:
                    data = json.loads(json_str)
                    ad_data = data.get("ad", {})
                    room_info['id'] = ad_data.get('id')
                    room_info['price'] = ad_data.get('price')
                    room_info['typology'] = ad_data.get('typology') # e.g., '7' for room
                    
                    characteristics = ad_data.get('characteristics', {})
                    room_info['total_rooms_in_flat'] = characteristics.get('roomNumber')
                    room_info['total_bathrooms_in_flat'] = characteristics.get('bathNumber')
                    room_info['has_lift'] = True if characteristics.get('hasLift') == "1" else (False if characteristics.get('hasLift') == "0" else None)
                    room_info['constructed_area_m2'] = characteristics.get('constructedArea')
                    
                    media = ad_data.get('media', {})
                    room_info['num_photos'] = media.get('photoNumber')
                    
                    owner = ad_data.get('owner', {})
                    room_info['owner_type'] = owner.get('type') # '1' usually private, '2' professional/agency
                    room_info['owner_commercial_name'] = owner.get('commercialName')
                    room_info['chat_is_active'] = True if owner.get('chatIsActive') == "1" else False

                except json.JSONDecodeError as e:
                    print(f"Error decoding utag_data JSON: {e}")
        
        title_element = soup.select_one("h1 span.main-info__title-main")
        if title_element:
            room_info['title_detail'] = title_element.get_text(strip=True)

        location_minor_element = soup.select_one(".main-info__title-minor")
        if location_minor_element:
            room_info['location_string'] = location_minor_element.get_text(strip=True)

        price_display_element = soup.select_one(".info-data-price .txt-bold")
        if price_display_element:
            room_info['price_display_value'] = price_display_element.get_text(strip=True)
        
        price_full_element = soup.select_one(".info-data-price")
        if price_full_element:
            full_price_text = price_full_element.get_text(strip=True)
            room_info['price_display_full'] = full_price_text
            # Attempt to extract unit (e.g., €/mês)
            if room_info.get('price_display_value') and room_info.get('price_display_value') in full_price_text:
                 room_info['price_display_unit'] = full_price_text.replace(room_info['price_display_value'], '').strip()

        features_div = soup.select_one(".info-features") # This is usually present and reliable
        if not features_div: # Fallback to the one near the price in <p class="info-data txt-big">
            features_div = soup.select_one("p.info-data.txt-big")

        if features_div:
            feature_spans = features_div.find_all("span", recursive=False)
            features_texts_display = []
            for span in feature_spans:
                text = span.get_text(strip=True)
                if text and not text.endswith('€/mês'): # Avoid capturing price again
                    features_texts_display.append(text)
            
            room_info['basic_features_display'] = features_texts_display
            
            for feat_text in features_texts_display:
                if "quart." in feat_text.lower():
                    room_info['num_rooms_highlighted'] = feat_text # e.g., "4 quart." (often total rooms in flat)
                elif "máx." in feat_text.lower():
                    room_info['max_tenants_in_room'] = feat_text # e.g., "2 máx."
                elif "não se pode fumar" in feat_text.lower():
                    room_info['smoking_allowed'] = False
                elif "pode fumar" in feat_text.lower(): # Less common to state explicitly
                    room_info['smoking_allowed'] = True
            
            # If smoking policy not found via text, check for specific icon class
            if 'smoking_allowed' not in room_info:
                if features_div.select_one(".icon-no-smokers"):
                    room_info['smoking_allowed'] = False
                elif features_div.select_one(".icon-smokers"): # Hypothetical class for allowed
                    room_info['smoking_allowed'] = True

        # --- "Estão à procura..." (Tenant Profile Requirements) ---
        tenant_profile_section = soup.select_one("div.details-property-feature-one h2.details-property-h2 + div.details-property_features ul")
        if not tenant_profile_section: # Alternative selector if the above fails
            heading = soup.find("h2", class_="details-property-h2", string=re.compile(r"Estão à procura\.\.\.", re.I))
            if heading:
                ul_container = heading.find_next_sibling("div", class_="details-property_features")
                if ul_container:
                    tenant_profile_section = ul_container.select_one("ul")
        
        if tenant_profile_section:
            profile_items_texts = [li.get_text(strip=True) for li in tenant_profile_section.find_all("li") if li.get_text(strip=True)]
            room_info['tenant_profile_requirements_raw'] = profile_items_texts
            for item_text in profile_items_texts:
                if re.search(r"entre\s+\d+\s+e\s+\d+\s+anos", item_text, re.I):
                    room_info['looking_for_age_range'] = item_text
                elif re.search(r"rapaz ou rapariga", item_text, re.I) or re.search(r"menino ou menina", item_text, re.I): # PT vs BR-PT variations
                    room_info['looking_for_gender_preference'] = item_text
                elif re.search(r"estad[ií]a mínima", item_text, re.I): # "Estadía mínima de 6 meses ou mais"
                    room_info['minimum_stay_requirement'] = item_text
        
        # --- Availability and other features in the second column (often under "details-property-feature-two") ---
        availability_section_ul = soup.select_one("div.details-property-feature-two div.details-property_features ul")
        if availability_section_ul:
            availability_items_texts = [li.get_text(strip=True) for li in availability_section_ul.find_all("li") if li.get_text(strip=True)]
            room_info['availability_and_other_features_raw'] = availability_items_texts
            for item_text in availability_items_texts:
                if re.search(r"já disponível", item_text, re.I) or re.search(r"disponível agora", item_text, re.I):
                    room_info['availability_status'] = "Já disponível" # Normalize
                # Add more parsing here if other structured items appear consistently

        # --- Advertiser Name ---
        # Prefer commercial name from utag_data if it's an agency
        if room_info.get('owner_type') == '2' and room_info.get('owner_commercial_name'):
            room_info['advertiser_name'] = room_info['owner_commercial_name']
        else:
            chat_banner_advertiser = soup.select_one(".chat-info-banner-text strong")
            if chat_banner_advertiser:
                room_info['advertiser_name'] = chat_banner_advertiser.get_text(strip=True)
            # Fallback if still no name and it's a private owner from utag_data
            elif room_info.get('owner_type') == '1' and not room_info.get('advertiser_name'):
                room_info['advertiser_name'] = "Particular" # Generic for private owner

        # --- Property Description ---
        description_text = None
        desc_selectors = [
            "div.ad_description_text",
            "div.comments div.comment > div:not([class])", # Text directly in a div under div.comment
            "div.comments div.comment", 
            "section#description .description-text",
            ".description .section-description",
            "div.view-more-text-description",
            "div[itemprop='description']" # Using itemprop
        ]
        for selector in desc_selectors:
            description_element = soup.select_one(selector)
            if description_element:
                # Remove "Ver mais", "Read more", etc., buttons/links before getting text
                for more_link_btn in description_element.select('a[class*="more"], button[class*="more"], a[class*="expand"], button[class*="expand"]'):
                    more_link_btn.decompose()
                description_text = description_element.get_text(separator="\n", strip=True)
                if len(description_text) > 50: # Basic check for meaningful content
                    break
                else:
                    description_text = None # Reset if too short, try next selector
        
        room_info['description'] = description_text

        # --- Image URLs ---
        room_info['image_urls'] = []
        # Main image (Desktop and Mobile often share similar structures or src)
        main_image_sources = soup.select(".main-image picture source[type='image/jpeg'], .main-image picture img")
        for main_img_tag in main_image_sources:
            url = None
            if main_img_tag.name == 'source' and main_img_tag.has_attr('srcset'):
                url = main_img_tag['srcset'].split(',')[0].split(' ')[0] # Get first URL from srcset
            elif main_img_tag.name == 'img' and main_img_tag.has_attr('src'):
                url = main_img_tag['src']
            
            if url and url.startswith('http') and url not in room_info['image_urls']:
                room_info['image_urls'].append(url)
        
        # Gallery images (often loaded via JS, but sometimes a few are in data attributes or script tags)
        # For now, we have num_photos. A full gallery scrape would be more involved.
        # Example: Extract from multimedia JSON if present
        gallery_script = soup.find('script', string=re.compile(r'initialMultimediaData', re.I))
        if gallery_script:
            match = re.search(r'var\s+initialMultimediaData\s*=\s*(\[.*?\]);', gallery_script.string, re.DOTALL)
            if match:
                try:
                    gallery_json_str = match.group(1)
                    gallery_data = json.loads(gallery_json_str)
                    for item in gallery_data:
                        if item.get('type') == 'IMAGE' and item.get('url'):
                            img_url = item['url']
                            img_url = re.sub(r'{.*?}', 'WEB_DETAIL', img_url) # Replace placeholder with a common size/action
                            if img_url not in room_info['image_urls']:
                                room_info['image_urls'].append(img_url)
                except Exception as e:
                    print(f"Could not parse gallery multimedia JSON: {e}")


        # --- Detailed Characteristics / Equipment (e.g., Mobilado, Cozinha equipada) ---
        room_info['detailed_characteristics_sections'] = []
        # Look for sections like "Características básicas", "Equipamento", "Características do quarto"
        # These are often h2 or h3 followed by a div.details-property_features > ul
        
        characteristic_headings = soup.select("section#details h2.details-property-h2, section#details h3.details-property-h3")
        if not characteristic_headings: # Broader search if specific section#details fails
            characteristic_headings = soup.select("h2.details-property-h2, h3.details-property-h3")

        processed_headings_text = set() # To avoid processing same section if selectors overlap

        for heading in characteristic_headings:
            heading_text = heading.get_text(strip=True)
            if not heading_text or heading_text in processed_headings_text:
                continue
            
            # Skip sections already handled more specifically
            if re.search(r"Estão à procura\.\.\.|Sobre o anunciante|Contactar|Localização", heading_text, re.I):
                continue

            # Find the associated list of features
            # Common pattern: heading -> div.details-property_features -> ul -> li
            list_container = heading.find_next_sibling("div", class_="details-property_features")
            if not list_container: # Sometimes the ul is a direct sibling or nested differently
                list_container = heading.find_next_sibling("ul")
                if not list_container: # Or the div is a more generic wrapper
                    parent_div = heading.parent
                    if parent_div:
                        list_container = parent_div.select_one("div.details-property_features ul, ul.list-items-details")


            if list_container:
                ul_element = list_container.select_one("ul") if list_container.name != 'ul' else list_container
                if ul_element:
                    items = [li.get_text(strip=True) for li in ul_element.find_all("li") if li.get_text(strip=True)]
                    if items:
                        room_info['detailed_characteristics_sections'].append({
                            "section_title": heading_text,
                            "items": items
                        })
                        processed_headings_text.add(heading_text)

    except Exception as e:
        print(f"Error while extracting single room info: {e}")
        import traceback
        traceback.print_exc() # Provides more detail on the error
        # Return partially filled dict or None. Current setup returns room_info which might be partial.
        # Consider returning None explicitly on major failure if preferred.
        
    return room_info
    
def make_http_request(path: str): 
    print(f"Requesting: {path}")
    
    username = os.getenv("PROXY_USER")
    password = os.getenv("PROXY_PASSWORD")
    proxy = os.getenv("PROXY_URL")
    
    proxies = {
        "https": ('https://user-%s:%s@%s' % (username, password, proxy))
    }
    
    try:
        response = requests.get(path, headers=headers, cookies=cookies, proxies=proxies)
        
        if response.status_code == 200:
            print(f"Request successful: Status code {response.status_code}")
            return response
        else:
            print(f"Request failed: Status code {response.status_code}")
            print(f"Response headers: {response.headers}")
            print(f"Response content (first 500 chars): {response.text[:500]}")
            return None
    except Exception as e:
        print(f"Exception occurred: {e}")
        return None
    
def sendMessageToNFTYTopic(topic_name:str, title: str, message_to_send: str, ):
    body = json.dumps({
        "topic": topic_name,
        "message": message_to_send,
        "title": title,
    })

    requests.post("https://ntfy.sh/", data=body, headers=headers)
    print("Sent Request to NTFY with success")

def main():
    load_dotenv()
    global base_url
    base_url = os.getenv('BASE_URL')
    
    print("Getting all rooms")
    rooms = get_all_rooms()
    
    if not rooms or len(rooms) <= 0:
        print("No results fetched, skipping fetching details")
        return
    all_room_details = []
    
    for room in rooms:
        time.sleep(10)
        room_id = room["id"]
        print(f"Fetching room with id: {room_id}")
        details = get_room_details(room_id)
        if details:
            print(f"Successfully fetched details for room {room_id}")
            all_room_details.append(details)
            if len(all_room_details) == 1:
                 print(json.dumps(details, indent=2, ensure_ascii=False))
        else:
            print(f"Failed to fetch or parse details for room {room_id}")
            
    if len(all_room_details) > 0:
        # Create a timestamp for the filename
        current_time = datetime.datetime.now()
        timestamp = current_time.strftime("%Y-%m-%d @ %H-%M-%S")
        filename = f"results/room-details-{timestamp}.json"
        
        os.makedirs("results", exist_ok=True)
        
        with open(filename, "w", encoding="utf-8") as file:
            json.dump(all_room_details, file, indent=4, ensure_ascii=False)
            
    ntfy_topic_name = os.getenv('NTFY_TOPIC')
    sendMessageToNFTYTopic(ntfy_topic_name, "Rooms Scrapping Job", "Job ended with success, check the results")
    

if __name__ == "__main__":
    main()