import os
from string import Template
from typing import Optional, List

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")

def render_sos_template(
    name: str,
    blood_group: Optional[str],
    allergies: List[str],
    chronic_conditions: List[str],
    current_medications: List[str],
    emergency_contact_name: str,
    emergency_contact_phone: str
) -> str:
    """
    Renders the public SOS landing page template with patient medical details.
    """
    template_path = os.path.join(TEMPLATE_DIR, "sos_template.html")
    with open(template_path, "r", encoding="utf-8") as f:
        html_text = f.read()
    
    # Render lists of items
    allergies_list = "".join([f"<li>{allergy}</li>" for allergy in allergies]) or "<li>No documented allergies</li>"
    chronic_list = "".join([f"<li>{cond}</li>" for cond in chronic_conditions]) or "<li>No documented chronic conditions</li>"
    meds_list = "".join([f"<li>{med}</li>" for med in current_medications]) or "<li>No documented ongoing medications</li>"
    
    # Blood Group badge
    if blood_group:
        blood_badge_html = f'<div class="blood-badge">Blood Group: {blood_group}</div>'
    else:
        blood_badge_html = '<div class="blood-badge" style="background-color:#374151">Blood Group: N/A</div>'
        
    # Emergency call link
    if emergency_contact_phone and emergency_contact_phone != "Not Configured":
        emergency_phone_html = f'<a href="tel:{emergency_contact_phone}" class="emergency-phone">📞 {emergency_contact_phone}</a>'
    else:
        emergency_phone_html = '<div class="emergency-phone">Not Configured</div>'
        
    # Safely substitute placeholders in the template
    t = Template(html_text)
    return t.safe_substitute(
        name=name,
        blood_badge_html=blood_badge_html,
        allergies_list=allergies_list,
        chronic_list=chronic_list,
        meds_list=meds_list,
        ec_name=emergency_contact_name,
        emergency_phone_html=emergency_phone_html
    )
