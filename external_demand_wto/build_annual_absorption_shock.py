#!/usr/bin/env python3
"""Rebuild annual partner domestic-absorption and GDP foreign-demand shocks.

Sources: World Bank WDI API. Fixed destination weights are the audited
2005-2007 China export exposure weights excluding Hong Kong and Macao, frozen
from the native-WTO replication archive. Missing partner data are not zero;
the composite is renormalized over available weights and coverage is reported.
"""
from __future__ import annotations
import hashlib, json, time
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import requests

OUT=Path("external_demand_wto/annual_absorption_shock")
OUT.mkdir(parents=True,exist_ok=True)
WEIGHTS=json.loads("{\"AFG\":0.0002328047494745,\"AGO\":0.0006903554472565,\"ALB\":0.0002025871131278,\"ARE\":0.0088735797402641,\"ARG\":0.0031209183721048,\"ARM\":0.0001250162532643,\"AUS\":0.0203488066693227,\"AUT\":0.0045224964639715,\"AZE\":0.001179251025134,\"BDI\":3.51033462098e-05,\"BEL\":0.0131087960627318,\"BEN\":0.0005809845237729,\"BFA\":0.0002358086689368,\"BGD\":0.0021299482518388,\"BGR\":0.0011879376241632,\"BHR\":0.0014836553455704,\"BHS\":0.0001974334418113,\"BIH\":0.0002277903851724,\"BLR\":0.0005520199774034,\"BLZ\":2.71856979237e-05,\"BOL\":0.0002985775062724,\"BRA\":0.0056425661760638,\"BRB\":8.10483769349e-05,\"BRN\":0.0011765927146467,\"BTN\":4.29430370248e-05,\"BWA\":0.0003198617635558,\"CAF\":4.28351280106e-05,\"CAN\":0.0308407786161257,\"CHE\":0.0088084047544759,\"CHL\":0.0030342033718549,\"CIV\":0.0006821885192617,\"CMR\":0.0004751202647172,\"COD\":0.0002180305088976,\"COG\":0.0002876564571294,\"COL\":0.001769388283814,\"COM\":2.41489496292e-05,\"CPV\":1.65800516173e-05,\"CRI\":0.0006838142261307,\"CUB\":0.0011153153767342,\"CYP\":0.0001690784714649,\"CZE\":0.0022335089867557,\"DEU\":0.0527620522943136,\"DJI\":0.0001259566261705,\"DMA\":9.2447779714e-06,\"DNK\":0.0041599620910352,\"DOM\":0.0005361648044975,\"DZA\":0.0023187756309354,\"ECU\":0.000794379307593,\"EGY\":0.003540667910094,\"ERI\":2.46995962568e-05,\"ESP\":0.0157154630358947,\"EST\":0.0003726377678802,\"ETH\":0.001166071789444,\"FIN\":0.0031630977146451,\"FJI\":0.0001420668567619,\"FRA\":0.0201285148644992,\"FSM\":1.87579311791e-05,\"GAB\":0.0003235753746294,\"GBR\":0.0359731689084463,\"GEO\":0.0002587293054366,\"GHA\":0.0010998366616646,\"GIN\":0.0002825667474215,\"GMB\":3.97552625716e-05,\"GNB\":2.11314293303e-05,\"GNQ\":0.0001337149491008,\"GRC\":0.0019603548624627,\"GRD\":2.12566295664e-05,\"GTM\":0.0007120574530475,\"GUY\":4.38983616782e-05,\"HND\":0.0005336486107518,\"HRV\":0.0005685863354287,\"HTI\":0.0002226061316997,\"HUN\":0.0018045340178749,\"IDN\":0.0082684805893239,\"IND\":0.0161613315633939,\"IRL\":0.0022289759227699,\"IRN\":0.0058354611254647,\"IRQ\":0.0018943687744242,\"ISL\":0.0001067960748268,\"ISR\":0.0014947475730775,\"ITA\":0.022366037884903,\"JAM\":0.0001957607898761,\"JOR\":0.0007995200900826,\"JPN\":0.1136529691319985,\"KAZ\":0.0017586366047637,\"KEN\":0.0007759221306965,\"KGZ\":0.0002492540568094,\"KHM\":0.0016549996504459,\"KIR\":1.03751052046e-05,\"KNA\":8.9902267689e-06,\"KOR\":0.0481452394417049,\"KWT\":0.0021506507577283,\"LAO\":0.0004520046448303,\"LBN\":0.0007216592957403,\"LBR\":0.0001019424789377,\"LBY\":0.0006241990907946,\"LCA\":2.71302213547e-05,\"LKA\":0.0008498682084193,\"LSO\":7.50058924345e-05,\"LTU\":0.0005474211471808,\"LUX\":0.0004647845999332,\"LVA\":0.000393016422038,\"MAR\":0.0016250130620061,\"MDA\":0.0001927248255391,\"MDG\":0.0002287776495435,\"MDV\":5.52245583466e-05,\"MEX\":0.0244169968274792,\"MHL\":8.7759287512e-06,\"MKD\":0.0002370799619666,\"MLI\":0.0002638240098966,\"MLT\":0.0001306375361605,\"MMR\":0.0014116455388162,\"MNE\":8.24018108438e-05,\"MNG\":0.000349644932492,\"MOZ\":0.0003171181457407,\"MRT\":0.0001356090753598,\"MUS\":0.0002364697653159,\"MWI\":0.000121517895452,\"MYS\":0.0153736508458594,\"NAM\":0.0002466112741579,\"NER\":0.0001230077953109,\"NGA\":0.0025738415914066,\"NIC\":0.0002302925022868,\"NLD\":0.0393955093210357,\"NOR\":0.0029343945240589,\"NPL\":0.0004938782045967,\"NZL\":0.0024597848962125,\"OMN\":0.0012702324799147,\"PAK\":0.0021583822046528,\"PAN\":0.0005449012251169,\"PER\":0.0015110985236837,\"PHL\":0.0039020764645537,\"PLW\":6.1037355836e-06,\"PNG\":0.0002090277381986,\"POL\":0.002921480344179,\"PRT\":0.0014276246406789,\"PRY\":0.0003629267662332,\"QAT\":0.001238879407807,\"ROU\":0.0013585393097258,\"RUS\":0.0142736794414476,\"RWA\":0.0001372182537243,\"SAU\":0.0059430922245924,\"SDN\":0.0005729555220298,\"SEN\":0.0003711606278558,\"SGP\":0.0255282426244642,\"SLB\":2.43787784866e-05,\"SLE\":7.71640404553e-05,\"SLV\":0.0003707971895511,\"SOM\":5.86362754376e-05,\"SRB\":0.0007595234679869,\"SSD\":0.000105437328835,\"STP\":7.9948718961e-06,\"SUR\":8.97358312228e-05,\"SVK\":0.0007437509747491,\"SVN\":0.0005706418422511,\"SWE\":0.0044469227166146,\"SWZ\":7.60937842326e-05,\"SYC\":1.72690006384e-05,\"SYR\":0.0005991006055789,\"TCD\":0.0001545429783438,\"TGO\":0.0001202942390341,\"THA\":0.0132468247737101,\"TJK\":0.000182322499762,\"TKM\":0.0004030238420733,\"TLS\":2.71610007272e-05,\"TON\":1.26075305866e-05,\"TTO\":0.0003134916749087,\"TUN\":0.0006086696257442,\"TUR\":0.0095335989026583,\"TUV\":1.195377597e-06,\"TZA\":0.0005229706523576,\"UGA\":0.0005200397521379,\"UKR\":0.0013730303071853,\"URY\":0.000511474353567,\"USA\":0.2902645947955189,\"UZB\":0.0006841365897456,\"VCT\":1.37337614798e-05,\"VEN\":0.0027432323613733,\"VNM\":0.0102339390070225,\"VUT\":1.6627640918e-05,\"WSM\":1.60793613149e-05,\"YEM\":0.0007234873342649,\"ZAF\":0.0039933696560494,\"ZMB\":0.0003522731808751,\"ZWE\":0.0002832138039489}")
INDICATORS={
 "final_consumption_growth":"NE.CON.TOTL.KD.ZG",
 "capital_formation_growth":"NE.GDI.TOTL.KD.ZG",
 "final_consumption_share":"NE.CON.TOTL.ZS",
 "capital_formation_share":"NE.GDI.TOTL.ZS",
 "gdp_growth":"NY.GDP.MKTP.KD.ZG",
}
BASE="https://api.worldbank.org/v2/country/all/indicator/{code}?format=json&per_page=20000&date=2004:2024"

def fetch(session,name,code):
    url=BASE.format(code=code)
    last=None
    for attempt in range(5):
        try:
            r=session.get(url,timeout=90)
            r.raise_for_status()
            raw=r.content
            (OUT/f"{name}_raw.json").write_bytes(raw)
            payload=r.json()
            rows=[]
            for x in payload[1]:
                iso=str(x.get("countryiso3code") or "").upper()
                year=x.get("date")
                value=x.get("value")
                if iso in WEIGHTS and year and value is not None:
                    rows.append({"alpha3":iso,"year":int(year),name:float(value)})
            return pd.DataFrame(rows),{"name":name,"code":code,"url":url,"bytes":len(raw),
                "sha256":hashlib.sha256(raw).hexdigest(),"rows":len(rows)}
        except Exception as exc:
            last=exc
            if attempt<4: time.sleep(2**attempt)
    raise RuntimeError(f"Failed {name}: {last}")

def aggregate(panel,value_col):
    rows=[]
    for year,g in panel.groupby("year"):
        valid=g[value_col].notna() & g["fixed_weight"].notna()
        coverage=float(g.loc[valid,"fixed_weight"].sum())
        val=float((g.loc[valid,value_col]*g.loc[valid,"fixed_weight"]).sum()/coverage) if coverage>0 else None
        rows.append({"year":int(year),value_col:val,f"{value_col}_coverage":coverage,
                      f"{value_col}_market_count":int(valid.sum())})
    return pd.DataFrame(rows)

def main():
    s=requests.Session()
    s.headers.update({"User-Agent":"China-external-demand-annual-absorption-replication/1.0"})
    frames=[]; records=[]
    for name,code in INDICATORS.items():
        f,r=fetch(s,name,code); frames.append(f); records.append(r)
    panel=pd.DataFrame([(k,float(v)) for k,v in WEIGHTS.items()],columns=["alpha3","fixed_weight"])
    panel=panel.assign(key=1).merge(pd.DataFrame({"year":range(2004,2025),"key":1}),on="key").drop(columns="key")
    for f in frames:
        panel=panel.merge(f,on=["alpha3","year"],how="left",validate="one_to_one")
    panel=panel.sort_values(["alpha3","year"])
    panel["lag_final_consumption_share"]=panel.groupby("alpha3")["final_consumption_share"].shift(1)
    panel["lag_capital_formation_share"]=panel.groupby("alpha3")["capital_formation_share"].shift(1)
    denom=panel["lag_final_consumption_share"]+panel["lag_capital_formation_share"]
    panel["domestic_absorption_growth"]=(
        panel["final_consumption_growth"]*panel["lag_final_consumption_share"]/denom
        +panel["capital_formation_growth"]*panel["lag_capital_formation_share"]/denom
    )
    out=aggregate(panel[panel.year>=2005],"domestic_absorption_growth").merge(
        aggregate(panel[panel.year>=2005],"gdp_growth"),on="year",how="outer")
    panel.to_csv(OUT/"partner_wdi_panel.csv",index=False)
    out.to_csv(OUT/"annual_foreign_demand_shocks.csv",index=False)
    status={
      "generated_at_utc":datetime.now(timezone.utc).isoformat(),
      "status":"COMPLETE",
      "weight_count":len(WEIGHTS),
      "weight_sum":sum(WEIGHTS.values()),
      "definition":"Lagged consumption and capital-formation GDP shares weight current real growth rates; destination aggregation uses fixed 2005-2007 export weights excluding Hong Kong and Macao.",
      "downloads":records,
      "annual_rows":out.to_dict(orient="records"),
      "limitations":["Conditional aggregate demand measure, not an external instrument.",
                     "Missing partner data are excluded and weights are renormalized; coverage is reported."]
    }
    (OUT/"status.json").write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(status,ensure_ascii=False,indent=2))
if __name__=="__main__": main()
