TP Special Agent
Daily transfer pricing & international tax intelligence — powered by Gemini 2.0 Flash, published via GitHub Pages.
Live report: https://<your-username>.github.io/TP_special_agent/tp_report.html
Sister agent: AI Valuechain Agent

What it does
Every morning at 07:00 EET (05:00 UTC), a GitHub Actions workflow:

Pulls TP/tax news from specialist RSS feeds (OECD, MNE Tax, EU Tax Observatory, ITR, and more)
Scrapes targeted sources without RSS (KHO, EUR-Lex)
Filters to confirmed transfer pricing & taxation content only
Classifies each item with Gemini 2.0 Flash: assigns a TP lens, importance score (1–5), and AI summary
Checks whether article URLs are openly accessible (no paywall)
Renders a static HTML report and commits it to GitHub Pages


TP focus lenses
All content is transfer pricing and taxation. Lenses are secondary tags:

Intangibles & IP
Business restructuring
Finance & treasury
PE & attribution
AI & digital economy
Court decisions
Pillar Two / GloBE
Dispute resolution / MAP
Documentation & CbCR
General TP


Setup
1. Create the repository
bashgit clone https://github.com/<your-username>/TP_special_agent
cd TP_special_agent
2. Add your Gemini API key
Go to Settings → Secrets and variables → Actions → New repository secret

Name: GEMINI_API_KEY
Value: your key from Google AI Studio

3. Enable GitHub Pages
Go to Settings → Pages

Source: Deploy from a branch
Branch: main / / (root)

4. Trigger the first run
Go to Actions → Daily TP Special Agent Report → Run workflow
The report will be live at https://<your-username>.github.io/TP_special_agent/tp_report.html

Local development
bashpip install -r requirements.txt
export GEMINI_API_KEY=your_key_here
python scripts/run_agent.py
open tp_report.html

Cost

GitHub Actions: free (public repo, well within 2,000 min/month limit)
Gemini 2.0 Flash: free tier (1,500 requests/day — daily run uses ~5–10)
GitHub Pages: free

Total: $0/month


