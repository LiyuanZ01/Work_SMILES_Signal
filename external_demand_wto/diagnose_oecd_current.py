#!/usr/bin/env python3
import requests, pandas as pd
from io import BytesIO
urls={
'gdp_filtered':'https://sdmx.oecd.org/public/rest/data/OECD.SDD.NAD,DSD_NAMAIN1@DF_QNA_EXPENDITURE_GROWTH_G20,1.1/Q...S1..B1GQ.......?startPeriod=2005-Q1&endPeriod=2024-Q4&dimensionAtObservation=AllDimensions&format=csvfilewithlabels',
'gdp_all':'https://sdmx.oecd.org/public/rest/data/OECD.SDD.NAD,DSD_NAMAIN1@DF_QNA_EXPENDITURE_GROWTH_G20,1.1/?startPeriod=2005-Q1&endPeriod=2024-Q4&dimensionAtObservation=AllDimensions&format=csvfilewithlabels',
'ip_china':'https://sdmx.oecd.org/public/rest/data/OECD.SDD.STES,DSD_KEI@DF_KEI,4.0/CHN.M.PRVM.IX.BTE..?startPeriod=2005-01&endPeriod=2024-12&dimensionAtObservation=AllDimensions&format=csvfilewithlabels',
'ip_all':'https://sdmx.oecd.org/public/rest/data/OECD.SDD.STES,DSD_KEI@DF_KEI,4.0/.M.PRVM.IX.BTE..?startPeriod=2005-01&endPeriod=2024-12&dimensionAtObservation=AllDimensions&format=csvfilewithlabels',
}
s=requests.Session(); s.headers['User-Agent']='China-external-demand-oecd-diagnostic/1.0'
for name,url in urls.items():
    print('\n###',name,url,flush=True)
    try:
        r=s.get(url,timeout=180,headers={'Accept':'text/csv'})
        print('status',r.status_code,'type',r.headers.get('content-type'),'bytes',len(r.content),flush=True)
        print('prefix',repr(r.content[:160]),flush=True)
        if r.ok and r.content:
            try:
                df=pd.read_csv(BytesIO(r.content))
                print('shape',df.shape,flush=True)
                print('columns',df.columns.tolist(),flush=True)
                print(df.head(3).to_string(index=False),flush=True)
                for c in df.columns:
                    if c.upper() in {'REF_AREA','REFERENCE AREA','TIME_PERIOD','OBS_VALUE','FREQ','TRANSFORMATION','UNIT_MEASURE','TRANSACTION','ADJUSTMENT'}:
                        print(c,df[c].dropna().astype(str).unique()[:20].tolist(),flush=True)
                if 'REF_AREA' in df.columns:
                    sub=df[df['REF_AREA'].astype(str).eq('CHN')]
                    print('CHN rows',len(sub),flush=True)
                    print(sub.head(3).to_string(index=False),flush=True)
            except Exception as e: print('parse error',repr(e),flush=True)
    except Exception as e: print('request error',repr(e),flush=True)
