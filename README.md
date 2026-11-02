Access over Tailscale
SunShop is running and loads at http://100.69.150.61:8080 from this PC.

This PC was already on Tailscale (logged in as cnt25), and the network it's on is a large shared Cambridge one with over 100 devices. For lab members:

Install Tailscale from tailscale.com/download.
Sign in with their Cambridge account. That should put them on the same Tailscale network, but please confirm with one person first.
Open http://100.69.150.61:8080 and log in with their SunShop account.
Notes:

Use the number, not a name: .ts.net names don't work on this Tailscale network. The address won't change.
Other people on the shared network can reach the login page, but only staff can create accounts, so they can't get in.
I can't test from another device myself. Please try it on your phone with Tailscale installed and Wi-Fi turned off.
I updated serve.py to detect the Tailscale address automatically and use it in the links inside notification emails. Start the site with start_sunshop.bat as before.

