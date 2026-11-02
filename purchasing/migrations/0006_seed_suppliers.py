"""Start the allowed-supplier list with every supplier the university has approved our orders from
(the PO / requisition approvals in history/lab_purchases.csv, Nov 2025 - Oct 2026)."""
from django.db import migrations

NOTE = "On approved university POs / requisitions (procurement history 2025-26)."

# (name, procurement name(s) as on POs, website)
SUPPLIERS = [
    ("Additive-X", ["ADDITIVE X LTD"], "https://www.additive-x.com"),
    ("Amazon Business", ["Amazon Business EU S.à.r.l, UK Branch", "AMAZON EU SARL, UK BRANCH"], "https://www.amazon.co.uk/business"),
    ("Appleton Woods", ["APPLETON WOODS LTD"], "https://www.appletonwoods.co.uk"),
    ("Arco", ["ARCO LTD"], "https://www.arco.co.uk"),
    ("Banner Business Solutions", ["BANNER BUSINESS SOLUTIONS LTD T/A COMPLETE"], ""),
    ("BMG Labtech", ["BMG LABTECH LTD"], "https://www.bmglabtech.com"),
    ("Cambridge Bioscience", ["CAMBRIDGE BIOSCIENCE LTD"], "https://www.bioscience.co.uk"),
    ("Camlab", ["CAMLAB LTD"], "https://www.camlab.co.uk"),
    ("Dell", ["DELL CORPORATION LTD"], "https://www.dell.com/en-uk"),
    ("Dixon Science", ["DIXON GLASS LTD T/A DIXON SCIENCE"], "https://www.dixonscience.com"),
    ("Edmund Optics", ["EDMUND OPTICS LTD"], "https://www.edmundoptics.co.uk"),
    ("Fisher Scientific", ["FISHER SCIENTIFIC UK LTD"], "https://www.fishersci.co.uk"),
    ("Fluorochem", ["FLUOROCHEM LTD"], "https://www.fluorochem.co.uk"),
    ("Gilson Scientific", ["GILSON SCIENTIFIC LTD"], "https://www.gilson.com"),
    ("GPE Scientific", ["GPE SCIENTIFIC LTD"], ""),
    ("Greatcell Solar Materials", ["GREATCELL SOLAR MATERIALS PTY LTD"], "https://www.greatcellsolarmaterials.com"),
    ("Insight Direct", ["INSIGHT DIRECT (UK) LTD"], "https://www.uk.insight.com"),
    ("Merck Life Science (Sigma-Aldrich)", ["MERCK LIFE SCIENCE UK LTD", "Merck", "Sigma-Aldrich"], "https://www.sigmaaldrich.com/GB/en"),
    ("Mettler-Toledo", ["METTLER-TOLEDO LTD"], "https://www.mt.com/gb/en/home.html"),
    ("Mouser Electronics", ["MOUSER ELECTRONICS, INC."], "https://www.mouser.co.uk"),
    ("Ocean Optics", ["OCEAN OPTICS BV", "Ocean Insight"], "https://www.oceanoptics.com"),
    ("Onecall (Farnell)", ["ONECALL (FARNELL)", "Farnell"], "https://onecall.farnell.com"),
    ("Opentrons", ["OPENTRONS LABWORKS INC"], "https://opentrons.com"),
    ("Ossila", ["OSSILA LTD"], "https://www.ossila.com"),
    ("Printerbase", ["PRINTERBASE LTD"], "https://www.printerbase.co.uk"),
    ("Rapid Electronics", ["RAPID ELECTRONICS LTD", "Rapid Online"], "https://www.rapidonline.com"),
    ("RobotShop", ["ROBOTSHOP INC"], "https://www.robotshop.com"),
    ("RS Components", ["RS COMPONENTS LTD", "RS Online", "RS"], "https://uk.rs-online.com"),
    ("Scientific Laboratory Supplies", ["SCIENTIFIC LABORATORY SUPPLIES LTD", "SLS"], "https://www.scientificlabs.co.uk"),
    ("Thorlabs", ["THORLABS LTD"], "https://www.thorlabs.com"),
    ("Tokyo Chemical Industry (TCI)", ["TOKYO CHEMICAL INDUSTRY UK LTD", "TCI"], "https://www.tcichemicals.com"),
    ("VWR International", ["VWR INTERNATIONAL LTD", "Avantor"], "https://uk.vwr.com"),
    ("Wolf Laboratories", ["WOLF LABORATORIES LTD"], "https://www.wolflabs.co.uk"),
    ("XMA", ["XMA LTD"], "https://www.xma.co.uk"),
]


def seed(apps, schema_editor):
    Supplier = apps.get_model("purchasing", "Supplier")
    for name, aliases, website in SUPPLIERS:
        Supplier.objects.get_or_create(name=name, defaults={"aliases": "\n".join(aliases), "website": website, "notes": NOTE})


def unseed(apps, schema_editor):
    apps.get_model("purchasing", "Supplier").objects.filter(name__in=[s[0] for s in SUPPLIERS], notes=NOTE).delete()


class Migration(migrations.Migration):
    dependencies = [("purchasing", "0005_supplier")]
    operations = [migrations.RunPython(seed, unseed)]
