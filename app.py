import streamlit as st
import google.generativeai as genai
import pandas as pd
import json
import requests
from datetime import datetime
from xero_python.accounting import AccountingApi, Contact, LineItem, Invoice
from xero_python.accounting.models import LineAmountTypes
from xero_python.api_client import ApiClient, Configuration
from xero_python.api_client.oauth2 import OAuth2Token

st.set_page_config(page_title="Free Xero Bill OCR", layout="wide")
st.title("🆓 Free Xero Bill Creator (Gemini OCR)")

# ====================== GEMINI (free) ======================
gemini_key = st.text_input("Google Gemini API Key (free at https://aistudio.google.com)", type="password")
if gemini_key:
    genai.configure(api_key=gemini_key)

# ====================== XERO AUTH ======================
st.sidebar.header("Xero Connection")
client_id = st.sidebar.text_input("Xero Client ID")
client_secret = st.sidebar.text_input("Xero Client Secret", type="password")

if "xero_token" not in st.session_state:
    st.session_state.xero_token = None

redirect_uri = st.sidebar.text_input(
    "Redirect URI (your current app URL)",
    value="https://your-app-name.streamlit.app",
    help="Replace this with your actual live URL (e.g. https://sv73cegtejaoux4ldhrt.streamlit.app)"
)

if client_id and client_secret and st.sidebar.button("🔑 Connect to Xero"):
    auth_url = f"https://login.xero.com/identity/connect/authorize?response_type=code&client_id={client_id}&redirect_uri={redirect_uri}&scope=accounting.transactions offline_access&state=12345"
    st.sidebar.markdown(f"[Click here to log into Xero →]({auth_url})")
    st.sidebar.info("After login, copy the FULL browser URL and paste it below")

auth_code = st.sidebar.text_input("Paste the full redirect URL here")
if auth_code and client_id and client_secret and st.sidebar.button("Exchange for Token"):
    code = auth_code.split("code=")[1].split("&")[0] if "code=" in auth_code else auth_code.strip()
    token_url = "https://identity.xero.com/connect/token"
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri}
    response = requests.post(token_url, data=data, auth=(client_id, client_secret))
    if response.status_code == 200:
        tokens = response.json()
        st.session_state.xero_token = OAuth2Token(
            client_id=client_id, client_secret=client_secret,
            access_token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token"),
            expires_at=datetime.now().timestamp() + tokens["expires_in"]
        )
        st.sidebar.success("✅ Connected to Xero!")
    else:
        st.sidebar.error("Token exchange failed")

# ====================== OCR (runs immediately) ======================
uploaded_file = st.file_uploader("Upload invoice / bill (PDF or image)", type=["pdf", "png", "jpg", "jpeg"])

if uploaded_file and gemini_key:
    file_bytes = uploaded_file.read()
    mime_type = uploaded_file.type

    model = genai.GenerativeModel("gemini-1.5-flash")
    prompt = """
    You are an expert Australian accountant. Extract this invoice as clean JSON only.
    Required keys:
    - supplier_name
    - invoice_number
    - invoice_date (YYYY-MM-DD)
    - due_date (YYYY-MM-DD or null)
    - line_items: array of objects with exactly: description, quantity (number), unit_amount (number), line_total
    Return ONLY valid JSON, no extra text.
    """

    with st.spinner("Running Gemini OCR on your invoice..."):
        response = model.generate_content([prompt, {"mime_type": mime_type, "data": file_bytes}])
        try:
            raw = response.text.strip("```json").strip("```").strip()
            data = json.loads(raw)
            st.success("✅ Gemini OCR complete – review & edit below")

            col1, col2 = st.columns(2)
            with col1:
                supplier = st.text_input("Supplier", data.get("supplier_name", ""))
                inv_num = st.text_input("Invoice #", data.get("invoice_number", ""))
            with col2:
                inv_date = st.date_input("Invoice Date", pd.to_datetime(data.get("invoice_date")))
                due_date = st.date_input("Due Date", pd.to_datetime(data.get("due_date")) if data.get("due_date") else datetime.now())

            st.subheader("Xero posting settings")
            default_account = st.text_input("Default Account Code", value="200")
            default_tax = st.selectbox("Default Tax Type (GST)", ["INPUT", "INPUT2", "EXEMPTINPUT", "NONE"], index=0)
            bill_status = st.selectbox("Create bill as", ["DRAFT", "AUTHORISED"], index=0)

            df = pd.DataFrame(data.get("line_items", []))
            if df.empty:
                df = pd.DataFrame(columns=["description", "quantity", "unit_amount", "line_total"])
            df["account_code"] = default_account
            df["tax_type"] = default_tax

            edited_df = st.data_editor(df, num_rows="dynamic", use_container_width=True)

            if st.button("🚀 Create Bill Directly in Xero", type="primary"):
                if not st.session_state.get("xero_token"):
                    st.error("Please connect to Xero in the sidebar first")
                else:
                    config = Configuration(oauth2_token=st.session_state.xero_token)
                    api_client = ApiClient(config)
                    accounting_api = AccountingApi(api_client)

                    line_items = []
                    for _, row in edited_df.iterrows():
                        line_items.append(LineItem(
                            description=str(row["description"]),
                            quantity=float(row["quantity"]),
                            unit_amount=float(row["unit_amount"]),
                            account_code=str(row["account_code"]),
                            tax_type=str(row["tax_type"])
                        ))

                    invoice = Invoice(
                        type="ACCPAY",
                        contact=Contact(name=supplier),
                        invoice_number=inv_num,
                        date=inv_date.strftime("%Y-%m-%d"),
                        due_date=due_date.strftime("%Y-%m-%d"),
                        line_amount_types=LineAmountTypes.INCLUSIVE,
                        line_items=line_items,
                        status=bill_status
                    )

                    result = accounting_api.create_invoices(xero_tenant_id=None, invoices=[invoice])
                    created = result.invoices[0]
                    st.success(f"✅ Bill created as {bill_status}!")
                    st.markdown(f"[Open the bill in Xero →](https://go.xero.com/AccountsPayable/Edit.aspx?InvoiceID={created.invoice_id})")
        except Exception as e:
            st.error(f"OCR error: {e}")

st.caption("100% free • Gemini OCR • Direct to Xero")
