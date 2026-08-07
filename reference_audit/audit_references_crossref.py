#!/usr/bin/env python3
from __future__ import annotations
import requests, json, csv, re, unicodedata, time, hashlib
from pathlib import Path
from urllib.parse import quote
OUT=Path('reference_audit/results'); OUT.mkdir(parents=True,exist_ok=True)
REFS=[
{'key':'Adao2019','year':2019,'title':'Shift-share designs: Theory and inference','journal':'Quarterly Journal of Economics','volume':'134','issue':'4','pages':'1949-2010','doi':'10.1093/qje/qjz025'},
{'key':'Amiti2019','year':2019,'title':'The impact of the 2018 tariffs on prices and welfare','journal':'Journal of Economic Perspectives','volume':'33','issue':'4','pages':'187-210','doi':'10.1257/jep.33.4.187'},
{'key':'BlangaGubbay2023','year':2023,'title':'Is the global economy fragmenting?','journal':'WTO Staff Working Paper ERSD-2023-10','volume':'','issue':'','pages':'','doi':'10.30875/25189808-10'},
{'key':'Borusyak2022','year':2022,'title':'Quasi-experimental shift-share research designs','journal':'Review of Economic Studies','volume':'89','issue':'1','pages':'181-213','doi':'10.1093/restud/rdab030'},
{'key':'Brandt2013','year':2013,'title':'Factor market distortions across time, space and sectors in China','journal':'Review of Economic Dynamics','volume':'16','issue':'1','pages':'39-58','doi':'10.1016/j.red.2012.10.002'},
{'key':'Caselli2020','year':2020,'title':'Diversification through trade','journal':'Quarterly Journal of Economics','volume':'135','issue':'1','pages':'449-502','doi':'10.1093/qje/qjz028'},
{'key':'Constantinescu2020','year':2020,'title':'The global trade slowdown: Cyclical or structural?','journal':'World Bank Economic Review','volume':'34','issue':'1','pages':'121-142','doi':'10.1093/wber/lhx027'},
{'key':'Eichengreen2014','year':2014,'title':'Growth slowdowns redux','journal':'Japan and the World Economy','volume':'32','issue':'','pages':'65-84','doi':'10.1016/j.japwor.2014.07.003'},
{'key':'Fajgelbaum2020','year':2020,'title':'The return to protectionism','journal':'Quarterly Journal of Economics','volume':'135','issue':'1','pages':'1-55','doi':'10.1093/qje/qjz036'},
{'key':'GarciaHerrero2022','year':2022,'title':'Slowbalisation in the context of US-China decoupling','journal':'Intereconomics','volume':'57','issue':'6','pages':'352-358','doi':'10.1007/s10272-022-1086-x'},
{'key':'GoldbergReed2023','year':2023,'title':'Is the global economy deglobalizing? And if so, why? And what is next?','journal':'Policy Research Working Paper 10392','volume':'','issue':'','pages':'','doi':'10.1596/1813-9450-10392'},
{'key':'JetinReyes2020','year':2020,'title':'Wage-led demand as a rebalancing strategy for economic growth in China','journal':'Journal of Post Keynesian Economics','volume':'43','issue':'3','pages':'341-366','doi':'10.1080/01603477.2020.1774392'},
{'key':'Kramarz2020','year':2020,'title':'Volatility in the small and in the large: The lack of diversification in international trade','journal':'Journal of International Economics','volume':'122','issue':'','pages':'103276','doi':'10.1016/j.jinteco.2019.103276'},
{'key':'MacKinnonWebb2018','year':2018,'title':'The wild bootstrap for few (treated) clusters','journal':'The Econometrics Journal','volume':'21','issue':'2','pages':'114-135','doi':'10.1111/ectj.12107'},
{'key':'NeweyWest1987','year':1987,'title':'A simple, positive semi-definite, heteroskedasticity and autocorrelation consistent covariance matrix','journal':'Econometrica','volume':'55','issue':'3','pages':'703-708','doi':'10.2307/1913610'},
{'key':'Song2011','year':2011,'title':'Growing like China','journal':'American Economic Review','volume':'101','issue':'1','pages':'196-233','doi':'10.1257/aer.101.1.196'},
{'key':'WorldBank2024','year':2024,'title':'World Development Report 2024: The Middle-Income Trap','journal':'World Bank','volume':'','issue':'','pages':'','doi':'10.1596/978-1-4648-2078-6'},
{'key':'Zhu2012','year':2012,'title':"Understanding China's growth: Past, present, and future",'journal':'Journal of Economic Perspectives','volume':'26','issue':'4','pages':'103-124','doi':'10.1257/jep.26.4.103'}]
NONDOI=[
{'key':'DBnomics2026','url':'https://api.db.nomics.world/v22/series/WTO/ITS_MTP_QXVSA/156.TO.000.Q?observations=1','expected':'DBnomics WTO provider mirror'},
{'key':'NBS2016_2025','url':'https://www.stats.gov.cn/sj/ndsj/','expected':'China Statistical Yearbook'},
{'key':'OECD2026','url':'https://sdmx.oecd.org/public/rest/data/OECD.SDD.NAD,DSD_NAMAIN1@DF_QNA_EXPENDITURE_GROWTH_G20,1.1/Q.Y.CHN.S1.S1.B1GQ._Z._Z._Z.PC.L.G1.T0102?startPeriod=2005-Q1&endPeriod=2024-Q4&dimensionAtObservation=AllDimensions','expected':'Quarterly real GDP growth - G20 countries'},
{'key':'OWID2026','url':'https://ourworldindata.org/grapher/china-imports-as-share-of-gdp.csv?v=1&csvType=full&useColumnShortNames=false','expected':'China imports as a share of GDP'},
{'key':'WDI2026','url':'https://api.worldbank.org/v2/country/all/indicator/NY.GDP.MKTP.CD?format=json&per_page=10&date=2024','expected':'World Development Indicators GDP'},
{'key':'WTO2026_metadata','url':'https://www.wto.org/english/res_e/statis_e/merch_trade_stat_e.htm','expected':'Quarterly merchandise trade volume metadata'},
{'key':'WTO2026_portal','url':'https://stats.wto.org/en','expected':'WTO Stats portal'}]
def norm(s):
    s=unicodedata.normalize('NFKD',s or '').encode('ascii','ignore').decode().lower(); return re.sub(r'[^a-z0-9]+',' ',s).strip()
def sim(a,b):
    A=set(norm(a).split()); B=set(norm(b).split()); return len(A&B)/max(1,len(A|B))
session=requests.Session(); session.headers.update({'User-Agent':'China-external-demand-reference-audit/1.0'})
rows=[]; raw={}
for ref in REFS:
    url='https://api.crossref.org/works/'+quote(ref['doi'],safe=''); row={'key':ref['key'],'doi':ref['doi'],'expected_title':ref['title'],'expected_year':ref['year'],'expected_journal':ref['journal'],'expected_volume':ref['volume'],'expected_issue':ref['issue'],'expected_pages':ref['pages'],'crossref_url':url}
    try:
        r=session.get(url,timeout=60); row['http_status']=r.status_code; r.raise_for_status(); msg=r.json()['message']; raw[ref['key']]=msg; title=(msg.get('title') or [''])[0]; container=(msg.get('container-title') or [''])[0]; year=None
        for fld in ['published-print','published-online','issued','created']:
            if msg.get(fld,{}).get('date-parts'): year=msg[fld]['date-parts'][0][0]; break
        row.update({'crossref_title':title,'crossref_journal':container,'crossref_year':year,'crossref_volume':msg.get('volume',''),'crossref_issue':msg.get('issue',''),'crossref_pages':msg.get('page') or msg.get('article-number') or '','title_similarity':round(sim(ref['title'],title),4),'journal_similarity':round(sim(ref['journal'],container),4)})
        mismatches=[]
        if row['title_similarity']<0.75: mismatches.append('title')
        if year and int(year)!=int(ref['year']): mismatches.append('year')
        if ref['volume'] and str(msg.get('volume',''))!=ref['volume']: mismatches.append('volume')
        if ref['issue'] and str(msg.get('issue',''))!=ref['issue']: mismatches.append('issue')
        if ref['pages'] and norm(ref['pages'])!=norm(str(row['crossref_pages'])): mismatches.append('pages/article_number')
        row['status']='PASS' if not mismatches else 'CHECK: '+','.join(mismatches)
    except Exception as e: row['status']='ERROR'; row['error']=repr(e)
    rows.append(row); time.sleep(0.2)
urlrows=[]
for item in NONDOI:
    row=dict(item)
    try:
        r=session.get(item['url'],timeout=90,allow_redirects=True); row.update({'http_status':r.status_code,'final_url':r.url,'content_type':r.headers.get('content-type'),'bytes':len(r.content),'sha256':hashlib.sha256(r.content).hexdigest(),'status':'PASS' if r.status_code==200 and len(r.content)>100 else 'CHECK'})
    except Exception as e: row.update({'status':'ERROR','error':repr(e)})
    urlrows.append(row)
fields=list(dict.fromkeys(k for r in rows for k in r.keys()))
with (OUT/'crossref_reference_audit.csv').open('w',encoding='utf-8',newline='') as f:
    w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
fields2=list(dict.fromkeys(k for r in urlrows for k in r.keys()))
with (OUT/'data_source_url_audit.csv').open('w',encoding='utf-8',newline='') as f:
    w=csv.DictWriter(f,fieldnames=fields2); w.writeheader(); w.writerows(urlrows)
(OUT/'crossref_raw_metadata.json').write_text(json.dumps(raw,ensure_ascii=False,indent=2),encoding='utf-8')
summary={'doi_references':len(rows),'doi_pass':sum(r['status']=='PASS' for r in rows),'doi_checks':[r for r in rows if r['status']!='PASS'],'data_sources':urlrows}
(OUT/'reference_audit_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(summary,ensure_ascii=False,indent=2))
