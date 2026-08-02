import os
import random
import io
from datetime import datetime
import streamlit as st
from supabase import create_client, Client

from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.barcode.qr import QrCodeWidget

# --- Initialize Supabase Client ---
@st.cache_resource
def init_supabase() -> Client:
    url = st.secrets.get("SUPABASE_URL") or os.getenv("SUPABASE_URL")
    key = st.secrets.get("SUPABASE_KEY") or os.getenv("SUPABASE_KEY")
    if not url or not key:
        return None
    return create_client(url, key)

supabase = init_supabase()

# --- Helper Functions ---
def num_to_words_indian_clean(num):
    num = int(round(num))
    if num == 0: return "ZERO RUPEES ONLY"
    units = ["", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE", "TEN", 
             "ELEVEN", "TWELVE", "THIRTEEN", "FOURTEEN", "FIFTEEN", "SIXTEEN", "SEVENTEEN", "EIGHTEEN", "NINETEEN"]
    tens = ["", "", "TWENTY", "THIRTY", "FORTY", "FIFTY", "SIXTY", "SEVENTY", "EIGHTY", "NINETY"]
    
    def convert_below_thousand(n):
        res = ""
        if n >= 100:
            res += units[n // 100] + " HUNDRED "
            n %= 100
        if n >= 20:
            res += tens[n // 10] + " "
            n %= 10
        if n > 0:
            res += units[n] + " "
        return res.strip()

    result = ""
    crore = num // 10000000; num %= 10000000
    lakh = num // 100000; num %= 100000
    thousand = num // 1000; num %= 1000

    if crore > 0: result += convert_below_thousand(crore) + (" CRORES " if crore > 1 else " CRORE ")
    if lakh > 0: result += convert_below_thousand(lakh) + (" LAKHS " if lakh > 1 else " LAKH ")
    if thousand > 0: result += convert_below_thousand(thousand) + " THOUSAND "
    if num > 0: result += convert_below_thousand(num)
    return f"{result.strip()} RUPEES ONLY"

# --- Cloud Database & Storage Functions ---
def upload_to_cloud(ref_no, pdf_bytes, filename, owner, est_date, final_total):
    """Uploads PDF to Supabase Bucket & logs metadata to Supabase DB"""
    if not supabase:
        return None
    
    # 1. Upload PDF file to Supabase Bucket
    storage_path = f"pdf_estimations/{filename}"
    supabase.storage.from_("estimations").upload(
        path=storage_path,
        file=pdf_bytes,
        file_options={"content-type": "application/pdf", "upsert": "true"}
    )
    
    # 2. Get Public URL
    pdf_url = supabase.storage.from_("estimations").get_public_url(storage_path)
    
    # 3. Log to Supabase Database
    data = {
        "ref_no": str(ref_no),
        "customer_name": str(owner.upper()),
        "est_date": str(est_date),
        "amount": float(final_total),
        "pdf_url": pdf_url
    }
    supabase.table("estimation_logs").upsert(data).execute()
    return pdf_url

def fetch_estimation_by_ref(ref_no):
    """Retrieves PDF and metadata from Supabase Cloud by REF NO"""
    if not supabase:
        return None, None
    
    response = supabase.table("estimation_logs").select("*").eq("ref_no", str(ref_no).strip()).execute()
    if response.data:
        record = response.data[0]
        storage_path = f"pdf_estimations/Estimation_{record['ref_no']}_{record['customer_name'].replace(' ', '_').replace('&', 'AND')}.pdf"
        try:
            pdf_data = supabase.storage.from_("estimations").download(storage_path)
            return record, pdf_data
        except Exception:
            return record, None
    return None, None

def fetch_all_logs():
    """Fetches full log history from Supabase Cloud"""
    if not supabase:
        return []
    response = supabase.table("estimation_logs").select("*").order("created_at", desc=True).execute()
    return response.data

# --- PDF Generation in Memory ---
def generate_estimation_pdf_bytes(owner, address, est_date, target_total, include_header=True):
    is_single_page = target_total < 1500000

    FIXED_ITEMS_MASTER = [
        ("Replacing sanitary fittings inside the toilets", "SETS", "SETS_UNITS", 0.08),
        ("3 course of oil bond distemper (Inside repaint)", "SQ. FT", "SQFT", 0.07),
        ("Providing & casting bathroom glazed tiles fixing etc.", "SQ. FT", "SQFT", 0.05),
        ("Interior works (Wardrobes, Modular Kitchen)", "JOB", "JOB_LOT", 0.12),
        ("Electrical fittings, cables, switches etc.", "JOB", "JOB_LOT", 0.07),
        ("Painting (exterior walls)", "SQ. FT", "SQFT", 0.06),
        ("New plumbing lines and fixtures", "JOB", "JOB_LOT", 0.05),
        ("Landscaping/Balcony improvements", "JOB", "JOB_LOT", 0.06),
        ("False ceiling work", "SQ. FT", "SQFT", 0.07),
        ("Flooring (Tiles/Marble)", "SQ. FT", "SQFT", 0.07),
        ("Providing & fixing teak wood show case", "UNIT", "SETS_UNITS", 0.06),
        ("2 course of snow cem paint (Outside repaint)", "SQ. FT", "SQFT", 0.04),
        ("Replacing sanitary fittings inside the kitchen", "SET", "SETS_UNITS", 0.05),
        ("Demolition and debris removal", "LOT", "JOB_LOT", 0.06),
        ("Wall plastering and finishing", "SQ. FT", "SQFT", 0.09)
    ]

    def calculate_quantity(category, total_amount):
        min_budget, max_budget = 1500000.0, 4500000.0
        ratio = max(0.0, min(1.0, (total_amount - min_budget) / (max_budget - min_budget)))
        ratio = max(0.0, min(1.0, ratio + random.uniform(-0.05, 0.05)))
        if category == "SQFT": return f"{round(650 + ratio * (2500 - 650))} SQ. FT"
        elif category == "SETS_UNITS":
            qty = round(1 + ratio * (5 - 1))
            return f"{qty} SETS" if qty > 1 else "1 SET"
        elif category == "JOB_LOT":
            qty = round(1 + ratio * (2 - 1))
            return f"{qty} JOB" if qty > 1 else "1 JOB"
        return "1 JOB"

    total_items_needed = 10 if is_single_page else 15
    processed_items = [(desc, calculate_quantity(cat, target_total), w) for desc, _, cat, w in FIXED_ITEMS_MASTER]
    random.shuffle(processed_items)
    processed_items = processed_items[:total_items_needed]

    subtotal_target = target_total / 1.18
    weights = [item[2] * random.uniform(0.85, 1.15) for item in processed_items]
    total_weight = sum(weights)
    norm_weights = [w / total_weight for w in weights]
    item_amounts = [round(subtotal_target * w) for w in norm_weights]
    item_amounts[-1] += round(subtotal_target) - sum(item_amounts)

    actual_subtotal = sum(item_amounts)
    actual_gst = round(actual_subtotal * 0.18)
    final_total = actual_subtotal + actual_gst

    now = datetime.now()
    ref_no = now.strftime("%H%M%d%m%Y")
    clean_owner_name = owner.replace(' ', '_').replace('&', 'AND')
    header_suffix = "Standard" if include_header else "NoHeader"
    filename = f"Estimation_{header_suffix}_{ref_no}_{clean_owner_name}.pdf"

    pdf_buffer = io.BytesIO()
    doc = SimpleDocTemplate(pdf_buffer, pagesize=letter, rightMargin=30, leftMargin=30, topMargin=15, bottomMargin=15)
    styles = getSampleStyleSheet()

    RED_COLOR, BLUE_COLOR, LIGHT_PINK, BORDER_BLUE = colors.HexColor("#DC2626"), colors.HexColor("#1E40AF"), colors.HexColor("#EC4899"), colors.HexColor("#2563EB")

    title_style = ParagraphStyle("Title", parent=styles["Heading1"], alignment=1, fontSize=28, leading=32, fontName="Helvetica-Bold", textColor=RED_COLOR)
    sub_style = ParagraphStyle("Sub", parent=styles["Normal"], alignment=1, fontSize=9, leading=12, fontName="Helvetica-Bold", textColor=BLUE_COLOR)
    gstin_style = ParagraphStyle("GSTIN", parent=styles["Normal"], alignment=1, fontSize=9, leading=12, fontName="Helvetica-Bold", textColor=LIGHT_PINK)
    ref_left_style = ParagraphStyle("RefLeft", parent=styles["Normal"], alignment=0, fontSize=10, leading=12, fontName="Helvetica")
    ref_right_style = ParagraphStyle("RefRight", parent=styles["Normal"], alignment=2, fontSize=10, leading=12, fontName="Helvetica")
    box_hdr_style = ParagraphStyle("BoxHdr", parent=styles["Normal"], alignment=1, fontSize=14, leading=16, fontName="Helvetica-Bold", textColor=colors.black)
    box_detail_style = ParagraphStyle("BoxDetail", parent=styles["Normal"], alignment=1, fontSize=12.5, leading=15, fontName="Helvetica-Bold", textColor=colors.black)
    
    if is_single_page:
        cell_12_bold_center = ParagraphStyle("Cell11BC", parent=styles["Normal"], alignment=1, fontSize=11, leading=13.5, fontName="Helvetica-Bold", textColor=colors.black)
        hdr_12_bold_center = ParagraphStyle("Hdr11BC", parent=styles["Normal"], alignment=1, fontSize=11, leading=13.5, fontName="Helvetica-Bold", textColor=colors.black)
        total_14_bold = ParagraphStyle("Total12B", parent=styles["Normal"], alignment=1, fontSize=12, leading=14.5, fontName="Helvetica-Bold", textColor=colors.black)
        words_13_bold_center = ParagraphStyle("Words11.5BC", parent=styles["Normal"], alignment=1, fontSize=11.5, leading=14, fontName="Helvetica-Bold", textColor=colors.black)
        terms_hdr_center = ParagraphStyle("TermsHdr9.5", parent=styles["Normal"], alignment=1, fontSize=9.5, leading=12, fontName="Helvetica-Bold", textColor=colors.black)
        terms_point_size_8 = ParagraphStyle("TermsPt8.5", parent=styles["Normal"], alignment=0, fontSize=8.5, leading=10.5, fontName="Helvetica-Bold", textColor=colors.black)
    else:
        cell_12_bold_center = ParagraphStyle("Cell12BC", parent=styles["Normal"], alignment=1, fontSize=12, leading=14, fontName="Helvetica-Bold", textColor=colors.black)
        hdr_12_bold_center = ParagraphStyle("Hdr12BC", parent=styles["Normal"], alignment=1, fontSize=12, leading=14, fontName="Helvetica-Bold", textColor=colors.black)
        total_14_bold = ParagraphStyle("Total14B", parent=styles["Normal"], alignment=1, fontSize=14, leading=16, fontName="Helvetica-Bold", textColor=colors.black)
        words_13_bold_center = ParagraphStyle("Words13BC", parent=styles["Normal"], alignment=1, fontSize=13, leading=16, fontName="Helvetica-Bold", textColor=colors.black)
        terms_hdr_center = ParagraphStyle("TermsHdr10", parent=styles["Normal"], alignment=1, fontSize=10, leading=14, fontName="Helvetica-Bold", textColor=colors.black)
        terms_point_size_8 = ParagraphStyle("TermsPt8", parent=styles["Normal"], alignment=0, fontSize=8, leading=11, fontName="Helvetica-Bold", textColor=colors.black)

    elements = []

    def create_header_with_qr():
        qr_data = f"CUSTOMER NAME: {owner.upper()}\nADDRESS: {address.upper()}\nREF NO: {ref_no}\nDATE: {est_date}\nESTIMATION AMOUNT: Rs. {final_total:,}"
        qr = QrCodeWidget(qr_data)
        qr_bounds = qr.getBounds()
        w, h = qr_bounds[2] - qr_bounds[0], qr_bounds[3] - qr_bounds[1]
        d = Drawing(60, 60, transform=[60.0/w, 0, 0, 60.0/h, 0, 0])
        d.add(qr)
        
        if include_header:
            header_text_flowables = [
                Paragraph("SND INTERIOR & DESIGNS", title_style), Spacer(1, 2),
                Paragraph("INTERIOR WORKS, DESIGN ESTIMATE, FLOOR VALUATIONS, BUILDING PLANS", sub_style),
                Paragraph("#15, E BLOCK, SAHAKHAR NAGAR, BANGALORE-560092", sub_style),
                Paragraph("GSTIN: 29ABCDE1234F1Z5", gstin_style),
            ]
        else:
            header_text_flowables = [Spacer(1, 50)]
            
        header_table = Table([["", header_text_flowables, d]], colWidths=[75, 400, 75])
        header_table.setStyle(TableStyle([('VALIGN', (0,0), (-1,-1), 'MIDDLE'), ('LEFTPADDING', (0,0), (-1,-1), 0), ('RIGHTPADDING', (0,0), (-1,-1), 0)]))
        return [header_table, Spacer(1, 4)]

    elements.extend(create_header_with_qr())
    elements.append(Table([[Paragraph(f"REF NO:-{ref_no}", ref_left_style), Paragraph(f"DATE: {est_date}", ref_right_style)]], colWidths=[270, 280]))
    elements.append(Spacer(1, 4))

    address_parts = [p.strip() for p in address.split(',')]
    mid_idx = len(address_parts) // 2
    addr_line_1 = ", ".join(address_parts[:mid_idx]) if mid_idx > 0 else address
    addr_line_2 = ", ".join(address_parts[mid_idx:]) if mid_idx > 0 else ""

    box_content = [[Paragraph("ESTIMATION FOR RENOVATION & INTERIOR DESIGN WORK AT", box_hdr_style)], [Paragraph("RESIDENTIAL FLAT AT", box_hdr_style)], [Paragraph(addr_line_1.upper(), box_detail_style)]]
    if addr_line_2: box_content.append([Paragraph(addr_line_2.upper(), box_detail_style)])
    box_content.append([Paragraph(f"OWNER: - {owner.upper()}", box_detail_style)])

    project_box = Table(box_content, colWidths=[550])
    project_box.setStyle(TableStyle([('BOX', (0,0), (-1,-1), 2, BORDER_BLUE), ('ROUNDEDCORNERS', [8, 8, 8, 8]), ('TOPPADDING', (0,0), (-1,-1), 3), ('BOTTOMPADDING', (0,0), (-1,-1), 3)]))
    elements.append(project_box)
    elements.append(Spacer(1, 6))

    if is_single_page:
        p_table_data = [[Paragraph("SL.NO", hdr_12_bold_center), Paragraph("Description", hdr_12_bold_center), Paragraph("Qty", hdr_12_bold_center), Paragraph("Amount Rs.", hdr_12_bold_center)]]
        for idx in range(10):
            item = processed_items[idx]
            p_table_data.append([Paragraph(f"{idx+1}.", cell_12_bold_center), Paragraph(item[0], cell_12_bold_center), Paragraph(item[1], cell_12_bold_center), Paragraph(f"{item_amounts[idx]:,}", cell_12_bold_center)])
        p_table_data.append(["", Paragraph("GST 18%", total_14_bold), "", Paragraph(f"{actual_gst:,}", total_14_bold)])
        p_table_data.append(["", Paragraph("TOTAL", total_14_bold), "", Paragraph(f"{final_total:,}", total_14_bold)])

        t1 = Table(p_table_data, colWidths=[50, 280, 100, 120])
        t1.setStyle(TableStyle([('GRID', (0,0), (-1,-1), 0.5, colors.black), ('VALIGN', (0,0), (-1,-1), 'MIDDLE'), ('TOPPADDING', (0,0), (-1,-1), 6.5), ('BOTTOMPADDING', (0,0), (-1,-1), 6.5)]))
        elements.append(t1)
    else:
        p1_table_data = [[Paragraph("SL.NO", hdr_12_bold_center), Paragraph("Description", hdr_12_bold_center), Paragraph("Qty", hdr_12_bold_center), Paragraph("Amount Rs.", hdr_12_bold_center)]]
        for idx in range(9):
            item = processed_items[idx]
            p1_table_data.append([Paragraph(f"{idx+1}.", cell_12_bold_center), Paragraph(item[0], cell_12_bold_center), Paragraph(item[1], cell_12_bold_center), Paragraph(f"{item_amounts[idx]:,}", cell_12_bold_center)])
        
        t1 = Table(p1_table_data, colWidths=[50, 280, 100, 120])
        t1.setStyle(TableStyle([('GRID', (0,0), (-1,-1), 0.5, colors.black), ('VALIGN', (0,0), (-1,-1), 'MIDDLE'), ('TOPPADDING', (0,0), (-1,-1), 16), ('BOTTOMPADDING', (0,0), (-1,-1), 16)]))
        elements.append(t1)

        elements.append(PageBreak())
        elements.extend(create_header_with_qr())

        p2_table_data = [[Paragraph("SL.NO", hdr_12_bold_center), Paragraph("Description", hdr_12_bold_center), Paragraph("Qty", hdr_12_bold_center), Paragraph("Amount Rs.", hdr_12_bold_center)]]
        for idx in range(9, 15):
            item = processed_items[idx]
            p2_table_data.append([Paragraph(f"{idx+1}.", cell_12_bold_center), Paragraph(item[0], cell_12_bold_center), Paragraph(item[1], cell_12_bold_center), Paragraph(f"{item_amounts[idx]:,}", cell_12_bold_center)])

        p2_table_data.append(["", Paragraph("GST 18%", total_14_bold), "", Paragraph(f"{actual_gst:,}", total_14_bold)])
        p2_table_data.append(["", Paragraph("TOTAL", total_14_bold), "", Paragraph(f"{final_total:,}", total_14_bold)])

        t2 = Table(p2_table_data, colWidths=[50, 280, 100, 120])
        t2.setStyle(TableStyle([('GRID', (0,0), (-1,-1), 0.5, colors.black), ('VALIGN', (0,0), (-1,-1), 'MIDDLE'), ('TOPPADDING', (0,0), (-1,-1), 16), ('BOTTOMPADDING', (0,0), (-1,-1), 16)]))
        elements.append(t2)

    elements.append(Spacer(1, 6))
    elements.append(Paragraph(num_to_words_indian_clean(final_total), words_13_bold_center))
    elements.append(Spacer(1, 5))
    elements.append(Paragraph("TERMS AND CONDITIONS:", terms_hdr_center))
    elements.append(Spacer(1, 3))

    terms_points = [
        "1. This Is A Preliminary Estimate And Not A Final Invoice",
        "2. Payment: 50% Advance, 30% After Material Delivery, 20% Upon Completion.",
        "3. Validity: This Estimation Is Valid For 45 Days From The Date Of Issue.",
        "4. Scope Of Work: Any Work Not Explicitly Mentioned In This Estimate Will Be Charged Extra.",
        "5. Materials: All Materials Used Will Be Of Standard Quality Unless Specified Otherwise.",
        "6. Project Duration: Estimated Project Completion Time Is 90 Working Days From Advance."
    ]
    for point in terms_points:
        elements.append(Paragraph(point, terms_point_size_8))
        elements.append(Spacer(1, 2))

    doc.build(elements)
    pdf_bytes = pdf_buffer.getvalue()
    pdf_buffer.close()

    return pdf_bytes, filename, ref_no, final_total


# --- Streamlit Web UI ---
st.set_page_config(page_title="SND Estimation Cloud Generator", page_icon="☁️", layout="centered")

st.title("🏗️ SND Interior & Designs")
st.markdown("##### Cloud-Connected Estimation System")

if not supabase:
    st.warning("⚠️ **Cloud Storage Disconnected:** Please configure `SUPABASE_URL` and `SUPABASE_KEY` in Streamlit Secrets (`.streamlit/secrets.toml`) to enable permanent cloud storage.")

tab1, tab2 = st.tabs(["📝 Generate New Estimation", "🔍 Cloud Lookup by Ref No"])

# --- TAB 1: GENERATE NEW ESTIMATION ---
with tab1:
    with st.form("estimation_form"):
        owner_input = st.text_input("Owner Name:", value="KUSHAL ANAND & HKS")
        address_input = st.text_area("Site Address:", value="BIRLA TRIMAYA PHASE 4 FLAT-1205, T-6, F-12, DEVANAHALLI CHIKKAJALA, BENGALURU")
        date_input = st.text_input("Date:", value=datetime.now().strftime("%d-%m-%Y"))
        amount_input = st.number_input("Target Amount (Rs.):", min_value=50000, max_value=10000000, value=1499000, step=10000)
        
        header_option = st.radio(
            "Select Output Format:",
            options=["Normal (With Company Header)", "Without Company Header (Leave Space)"],
            index=0
        )
        
        submitted = st.form_submit_button("Generate & Save to Cloud", type="primary", use_container_width=True)

    if submitted:
        with st.spinner('Generating estimation PDF and syncing to cloud...'):
            try:
                include_hdr = (header_option == "Normal (With Company Header)")
                pdf_bytes, filename, generated_ref, final_total = generate_estimation_pdf_bytes(
                    owner_input, address_input, date_input, float(amount_input), include_header=include_hdr
                )
                
                # Sync to Supabase Cloud
                if supabase:
                    cloud_url = upload_to_cloud(generated_ref, pdf_bytes, filename, owner_input, date_input, final_total)
                    st.success(f"✅ Estimation Generated & Uploaded to Cloud! (**REF NO:** `{generated_ref}`)")
                else:
                    st.success(f"✅ Estimation Generated Locally! (**REF NO:** `{generated_ref}`)")

                st.download_button(
                    label="📄 Download Estimation PDF",
                    data=pdf_bytes,
                    file_name=filename,
                    mime="application/pdf",
                    type="primary",
                    use_container_width=True
                )

            except Exception as e:
                st.error(f"An error occurred: {e}")

# --- TAB 2: LOOKUP & DOWNLOAD BY REF NO ---
with tab2:
    st.subheader("Cloud Storage Lookup")
    st.write("Enter any Reference Number (`REF NO`) to pull the exact PDF directly from cloud storage.")

    search_ref = st.text_input("Enter Reference Number (REF NO):", placeholder="e.g. 152329072026")
    
    if search_ref:
        if supabase:
            with st.spinner('Searching cloud database...'):
                record, pdf_data = fetch_estimation_by_ref(search_ref)
                
                if record:
                    st.success(f"🎯 Found Estimation in Cloud!")
                    st.info(f"**REF NO:** {record['ref_no']} | **Customer:** {record['customer_name']} | **Date:** {record['est_date']} | **Amount:** Rs. {record['amount']:,.2f}")
                    
                    if pdf_data:
                        st.download_button(
                            label=f"📥 Download PDF ({record['ref_no']})",
                            data=pdf_data,
                            file_name=f"Estimation_{record['ref_no']}_{record['customer_name']}.pdf",
                            mime="application/pdf",
                            type="primary",
                            use_container_width=True
                        )
                    elif record.get('pdf_url'):
                        st.markdown(f"[🔗 Direct Download Link]({record['pdf_url']})")
                else:
                    st.error(f"❌ No estimation found in cloud storage matching REF NO: `{search_ref.strip()}`")
        else:
            st.error("Supabase cloud connection is not configured.")

    st.markdown("---")
    st.subheader("📋 Live Cloud History")
    if supabase:
        all_logs = fetch_all_logs()
        if all_logs:
            st.dataframe(all_logs, use_container_width=True)
        else:
            st.caption("No estimations stored in cloud database yet.")
