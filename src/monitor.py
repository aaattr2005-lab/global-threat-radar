import os, re, json, time, requests
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "state" / "seen.json"
GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"
RANK = {"Unverified":1, "Likely":2, "Confirmed":3}
TIER1 = {"reuters.com","apnews.com","bbc.com","bbc.co.uk","france24.com","dw.com","aljazeera.com","bloomberg.com","skynews.com","arabnews.com"}
LOCATIONS = {
    "Saudi Arabia":"السعودية","Riyadh":"الرياض","Jeddah":"جدة","Abha":"أبها","Khamis Mushait":"خميس مشيط","Jazan":"جازان","Najran":"نجران",
    "Yemen":"اليمن","Sanaa":"صنعاء","Aden":"عدن","Iran":"إيران","Tehran":"طهران","Iraq":"العراق","Baghdad":"بغداد",
    "Israel":"إسرائيل","Lebanon":"لبنان","Beirut":"بيروت","Syria":"سوريا","Damascus":"دمشق","Jordan":"الأردن",
    "Ukraine":"أوكرانيا","Kyiv":"كييف","Russia":"روسيا","Moscow":"موسكو","Poland":"بولندا","Romania":"رومانيا",
    "India":"الهند","Pakistan":"باكستان","China":"الصين","Taiwan":"تايوان","Japan":"اليابان","South Korea":"كوريا الجنوبية","North Korea":"كوريا الشمالية",
    "United States":"الولايات المتحدة","United Kingdom":"بريطانيا","France":"فرنسا","Germany":"ألمانيا","Turkey":"تركيا","Egypt":"مصر","Sudan":"السودان"
}
STOP={"the","a","an","of","in","on","at","to","for","from","with","and","or","after","as","by","says","said","report","reports","reported","breaking","live","latest","new","near","into","amid","is","are","was","were","this","that"}

def tokens(s):
    s=re.sub(r"[^a-z0-9\u0600-\u06FF\s-]"," ",(s or "").lower())
    return list(dict.fromkeys(x for x in s.split() if len(x)>2 and x not in STOP))

def jac(a,b):
    A,B=set(a),set(b)
    return len(A&B)/len(A|B) if A and B else 0

def etype(t):
    s=t.lower(); drone=re.search(r"\b(drone|uav|uas|shahed)\b",s); missile=re.search(r"\b(missile|ballistic|cruise missile|rocket)\b",s); inter=re.search(r"\b(intercept|interception|shot down|downed|air defense|air defence)\b",s); alert=re.search(r"\b(air raid|siren|warning|take shelter)\b",s)
    if inter and drone:return "Drone interception"
    if inter and missile:return "Missile interception"
    if drone:return "Drone"
    if missile:return "Missile"
    if alert:return "Air-raid alert"

def location(t):
    for k,v in LOCATIONS.items():
        if re.search(rf"\b{re.escape(k.lower())}\b",t.lower()): return v
    return "غير محدد"

def official(d):
    d=(d or "").lower()
    return bool(re.search(r"(^|\.)gov(\.|$)|(^|\.)mil(\.|$)|\.(gov|mil)\.[a-z]{2,3}$",d) or d.endswith(".gov.sa"))

def score(a):
    d=(a.get("domain") or "").lower(); t=(a.get("title") or "").lower(); s=100 if official(d) else (45 if d in TIER1 else 20)
    if re.search(r"\b(ministry|military|civil defense|civil defence|official statement|government says)\b",t): s+=20
    if re.search(r"\b(unconfirmed|reportedly|alleged|claims?)\b",t): s-=15
    return max(0,s), official(d)

def load_state():
    try:return json.loads(STATE.read_text(encoding="utf-8"))
    except:return {"events":[]}

def save_state(x): STATE.write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding="utf-8")

def send(msg):
    tok=os.environ["TELEGRAM_BOT_TOKEN"]; chat=os.environ["TELEGRAM_CHAT_ID"]
    r=requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",json={"chat_id":chat,"text":msg,"disable_web_page_preview":True},timeout=30); r.raise_for_status()

def main():
    q='(missile OR "ballistic missile" OR "cruise missile" OR "attack drone" OR "suicide drone" OR UAV OR UAS OR "air raid") (launch OR attack OR strike OR intercept OR interception OR siren OR alert OR warning OR "shot down")'
    params={"query":q,"mode":"artlist","format":"json","maxrecords":"250","timespan":"15min","sort":"DateDesc"}
    data=requests.get(GDELT,params=params,timeout=30,headers={"User-Agent":"GlobalOSINTTelegram/1.0"}).json()
    rows=[]
    for a in data.get("articles",[]):
        t=(a.get("title") or "").strip(); u=(a.get("url") or "").strip(); typ=etype(t)
        if not t or not u or not typ: continue
        sc,off=score(a); rows.append({**a,"title":t,"url":u,"typ":typ,"loc":location(t),"tok":tokens(t),"sc":sc,"off":off,"domain":(a.get("domain") or "").lower()})
    clusters=[]
    for r in rows:
        hit=None
        for c in clusters:
            if c["typ"]==r["typ"] and (c["loc"]==r["loc"] or "غير محدد" in (c["loc"],r["loc"])) and max([jac(x["tok"],r["tok"]) for x in c["rows"]] or [0])>=0.34:
                hit=c; break
        if hit:
            hit["rows"].append(r)
            if hit["loc"]=="غير محدد" and r["loc"]!="غير محدد": hit["loc"]=r["loc"]
        else: clusters.append({"typ":r["typ"],"loc":r["loc"],"rows":[r]})
    state=load_state(); now=time.time(); state["events"]=[e for e in state.get("events",[]) if now-e.get("ts",0)<172800]
    minc=os.getenv("MIN_CONFIDENCE","Unverified"); maxa=int(os.getenv("MAX_ALERTS","10")); sent=0; changed=False
    for c in clusters:
        domains=sorted({r["domain"] for r in c["rows"] if r["domain"]}); best=max(c["rows"],key=lambda r:r["sc"]); sc=best["sc"]; off=any(r["off"] for r in c["rows"])
        if not off: sc += 40 if len(domains)>=3 else (30 if len(domains)==2 else 0)
        sc=min(100,sc); conf="Confirmed" if off or sc>=75 else ("Likely" if sc>=50 else "Unverified")
        if RANK[conf] < RANK[minc]: continue
        tt=tokens(" ".join(r["title"] for r in c["rows"])); prior=None
        for e in state["events"]:
            if now-e.get("ts",0)<7200 and e.get("typ")==c["typ"] and (e.get("loc")==c["loc"] or "غير محدد" in (e.get("loc"),c["loc"])) and jac(tt,e.get("tok",[]))>=0.42:
                prior=e; break
        if prior and RANK[conf] <= RANK.get(prior.get("conf","Unverified"),1): continue
        ar={"Confirmed":"مؤكد","Likely":"مرجح","Unverified":"غير مؤكد"}[conf]; icon={"Confirmed":"✅","Likely":"🟡","Unverified":"⚪"}[conf]; typ_ar={"Drone":"مسيّرة","Missile":"صاروخ","Drone interception":"اعتراض مسيّرة","Missile interception":"اعتراض صاروخ","Air-raid alert":"إنذار غارة/خطر جوي"}[c["typ"]]
        src="\n".join(f"{i+1}) {r['domain'] or 'source'}\n{r['url']}" for i,r in enumerate(sorted(c["rows"],key=lambda x:x["sc"],reverse=True)[:3]))
        head="🔄 تحديث حالة حدث سابق" if prior else "🚨 تنبيه OSINT"
        msg=f"{head}\n\n📍 الموقع: {c['loc']}\n🛰️ النوع: {typ_ar}\n{icon} الحالة: {ar} / {conf}\n📊 درجة الثقة: {sc}/100\n🧩 مصادر مستقلة: {len(domains)}\n\n📰 {best['title']}\n\nالمصادر:\n{src}\n\n⚠️ معلومات OSINT وليست بديلاً عن التنبيهات الرسمية أو الدفاع المدني."
        send(msg)
        if prior: prior.update({"conf":conf,"tok":tt,"ts":now,"title":best["title"]})
        else: state["events"].append({"typ":c["typ"],"loc":c["loc"],"conf":conf,"tok":tt,"ts":now,"title":best["title"]})
        sent+=1; changed=True
        if sent>=maxa: break
    if changed: save_state(state)
    print(json.dumps({"articles":len(rows),"clusters":len(clusters),"alerts_sent":sent},ensure_ascii=False))
if __name__=="__main__": main()
