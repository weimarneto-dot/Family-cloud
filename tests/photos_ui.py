# fMily Cloud's Photos view in a real (headless) Firefox: upload through the page's own Upload photos button, the grid by day, the viewer
# (next, previous, arrow keys, swipe, delete and restore), My files still a list.
#   1. a throwaway Cloud:  echo 'correct horse' | ./server.py --data-dir /tmp/c add-user weimar --name Weimar --password-stdin
#                          ./server.py --data-dir /tmp/c serve --port 18778
#   2. pictures to upload in MEDIA (default /tmp/vt/media: JPEGs with EXIF dates on several days, one with orientation 6 named portrait.jpg,
#      one .mp4; test_photos.exif_jpeg makes the EXIF)
#   3. firefox --headless --marionette -remote-allow-system-access --no-remote --profile <dir>   (touch for the iphone run: user.js
#      ui.primaryPointerCapabilities 1, ui.allPointerCapabilities 1, dom.w3c_touch_events.enabled 1)
#   4. python3 tests/photos_ui.py [iphone]       (needs mari.py from family-home/tests/browser on MARI_DIR)
import sys, time, json, glob, os
sys.path.insert(0, os.environ.get("MARI_DIR", os.path.expanduser("~/family-home/tests/browser")))
from mari import M
PH = len(sys.argv) > 1 and sys.argv[1] == "iphone"
TAG = "iphone-" if PH else ""
def _js(self, code):          # no eval (the page's security policy forbids it): run it directly, as an expression if it is one
    try:
        return self.cmd("WebDriver:ExecuteScript", {"script": "return (" + code + ");", "args": []}).get("value")
    except RuntimeError as e:
        if "SyntaxError" not in str(e): raise
        return self.cmd("WebDriver:ExecuteScript", {"script": code, "args": []}).get("value")
M.js = _js
m = M()
try: m.cmd("WebDriver:NewSession", {"capabilities": {}})
except Exception: pass
fails = 0
def check(ok, what, extra=""):
    global fails
    print(("ok   " if ok else "FAIL ") + what + (("  [" + str(extra) + "]") if extra != "" else "")); fails += 0 if ok else 1
def zoom(z):
    m.cmd("Marionette:SetContext", {"value": "chrome"}); m.cmd("WebDriver:ExecuteScript", {"script": "gBrowser.selectedBrowser.fullZoom = %s; return 1;" % z, "args": []}); m.cmd("Marionette:SetContext", {"value": "content"})
def J(code): return json.loads(m.js("JSON.stringify(" + code + ")"))
def wait(code, secs=20):
    t = time.time()
    while time.time() - t < secs:
        try:
            v = m.js(code)
            if v: return v
        except Exception: pass
        time.sleep(0.3)
    return None
def shot(n): time.sleep(.6); os.makedirs("/tmp/vt/photos-shots", exist_ok=True); m.shot("/tmp/vt/photos-shots/" + TAG + n)
B = os.environ.get("CLOUD", "http://127.0.0.1:18778/")
m.cmd("WebDriver:Navigate", {"url": B}); time.sleep(1)
if PH: m.cmd("WebDriver:SetWindowRect", {"width": 523, "height": 1218}); zoom(1.25)
else: m.cmd("WebDriver:SetWindowRect", {"width": 1280, "height": 900}); zoom(1)
time.sleep(.5)
m.js("fetch('/api/logout',{method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'family-cloud'},body:'{}'}).then(()=>location.reload()); 1"); time.sleep(2)
if PH: zoom(1.25)
wait("!document.getElementById('login').hidden")
m.js("(()=>{const f=document.getElementById('login-form'); f.username.value='weimar'; f.password.value='correct horse'; f.requestSubmit(); return 1})()")
check(bool(wait("!document.getElementById('app').hidden")), "signed in", m.js("[innerWidth, innerHeight].join('x')"))
m.js("document.querySelector('#tabs [data-tab=photos]').click(); 1"); time.sleep(1)
check(m.js("!document.getElementById('upload-photos').hidden") and m.js("document.getElementById('upload').hidden"), "Photos tab: an Upload photos button (the plain Upload and New folder are gone)")
have = m.js("document.querySelectorAll('.ptile').length")
if not have:
    check(m.js("!document.getElementById('empty').hidden"), "empty Photos says how to add some", m.js("document.getElementById('empty').textContent"))
    shot("0-empty.png")
    # upload through the page's own photo input (what the phone's picker fills)
    m.js("document.getElementById('photo-file').removeAttribute('hidden'); 1")
    el = m.cmd("WebDriver:FindElement", {"using": "css selector", "value": "#photo-file"})["value"]
    eid = list(el.values())[0]
    files = sorted(glob.glob(os.path.join(os.environ.get("MEDIA", "/tmp/vt/media"), "*")))
    m.cmd("WebDriver:ElementSendKeys", {"id": eid, "text": "\n".join(files)})
    m.js("document.getElementById('photo-file').setAttribute('hidden', ''); 1")
    check(bool(wait("document.querySelectorAll('.ptile').length >= %d" % len(files), 90)), "all %d photos and the video uploaded and shown" % len(files), m.js("document.querySelectorAll('.ptile').length"))
    check(m.js("document.getElementById('uploads-title').textContent") == "Uploads finished", "the upload panel says finished", m.js("document.getElementById('uploads-title').textContent"))
    m.js("document.getElementById('uploads-close').click(); 1")
# the folder they went to
m.js("window.__r=null; api('/api/folder').then(r=>window.__r=r.items.map(i=>i.name)); 1"); wait("window.__r")
check("Photos" in J("window.__r"), "they went into a Photos folder in My files", J("window.__r"))
days = J("[...document.querySelectorAll('#photos .day')].map(h=>h.textContent)")
check(days[:2] == ["Today", "Yesterday"] and len(days) >= 4, "a heading per day, newest first (Today, Yesterday, ...)", days)
per = J("[...document.querySelectorAll('#photos .pgrid')].map(g=>g.children.length)")
print("tiles per day:", per)
g = J("(()=>{const t=[...document.querySelectorAll('.ptile')].slice(0,4).map(x=>{const r=x.getBoundingClientRect(); return [Math.round(r.left),Math.round(r.top),Math.round(r.width),Math.round(r.height)]}); return t})()")
check(all(abs(w - h) <= 1 for _, _, w, h in g), "tiles are square", g)
check(len(g) >= 3 and g[0][1] == g[1][1] == g[2][1], "side by side in a row", g)
cols = m.js("getComputedStyle(document.querySelector('.pgrid')).gridTemplateColumns.split(' ').length")
check((cols == 3) if PH else (cols >= 5), "%s columns across" % cols, cols)
check(m.js("document.documentElement.scrollWidth <= innerWidth + 1"), "no sideways scroll")
check(bool(wait("[...document.querySelectorAll('.ptile img')].slice(0,6).every(i=>i.complete && i.naturalWidth>0)", 30)), "thumbnails load")
pt = J("(()=>{const t=[...document.querySelectorAll('.ptile')].find(x=>/portrait/.test(x.getAttribute('aria-label'))); const i=t&&t.querySelector('img'); return i?[i.naturalWidth,i.naturalHeight]:null})()")
check(pt and pt[1] > pt[0], "the upright phone photo's thumbnail is upright (taller than wide)", pt)
check(m.js("document.querySelectorAll('.ptile.video').length") == 1, "the video has a play mark")
shot("1-grid.png")
# viewer: open, next, previous, keyboard
m.js("document.querySelectorAll('.ptile')[0].click(); 1"); time.sleep(.8)
name0 = m.js("document.getElementById('viewer-name').textContent")
check(m.js("!document.getElementById('viewer').hidden"), "tap a tile: the viewer opens", name0)
check(m.js("document.getElementById('viewer-prev').hidden") and (PH or m.js("!document.getElementById('viewer-next').hidden")), "first photo: no Previous, a Next")
shot("2-viewer.png")
m.js("document.getElementById('viewer-next').click(); 1"); time.sleep(.5)
name1 = m.js("document.getElementById('viewer-name').textContent")
check(name1 != name0, "Next goes to the next photo", [name0, name1])
m.js("document.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowLeft'})); 1"); time.sleep(.4)
check(m.js("document.getElementById('viewer-name').textContent") == name0, "the left arrow key goes back")
# swipe
m.js("""(()=>{const b=document.getElementById('viewer-body'); const t=(x)=>[new Touch({identifier:1,target:b,clientX:x,clientY:300})];
  b.dispatchEvent(new TouchEvent('touchstart',{touches:t(300),changedTouches:t(300),bubbles:true})); b.dispatchEvent(new TouchEvent('touchend',{touches:[],changedTouches:t(120),bubbles:true})); return 1})()""")
time.sleep(.4)
check(m.js("document.getElementById('viewer-name').textContent") == name1, "a swipe to the left goes to the next photo")
m.js("document.getElementById('viewer-close').click(); 1")
# delete from the viewer
n0 = m.js("document.querySelectorAll('.ptile').length")
m.js("document.querySelectorAll('.ptile')[1].click(); 1"); time.sleep(.5)
gone = m.js("document.getElementById('viewer-name').textContent")
m.js("document.getElementById('viewer-trash').click(); 1"); time.sleep(1)
check(m.js("document.querySelectorAll('.ptile').length") == n0 - 1 and not m.js("!!document.getElementById('viewer').hidden"), "Delete moves it to the trash and shows the next one", gone)
m.js("document.getElementById('viewer-close').click(); 1")
m.js("window.__t=null; api('/api/trash').then(r=>window.__t=r.map(i=>i.name)); 1"); wait("window.__t")
check(gone in J("window.__t"), "it is in the Trash (can be restored)")
m.js("window.__t2=null; api('/api/trash').then(async r=>{const it=r.find(i=>i.name==%s); await api('/api/restore',{id:it.id}); window.__t2=1}); 1" % json.dumps(gone)); wait("window.__t2")
# back to My files still works
m.js("document.querySelector('#tabs [data-tab=files]').click(); 1"); time.sleep(1)
check(m.js("document.getElementById('photos').hidden") and m.js("!document.getElementById('list').hidden") and m.js("!document.getElementById('upload').hidden"), "My files shows the list again")
m.js("document.querySelector('#tabs [data-tab=photos]').click(); 1"); time.sleep(1)
check(m.js("document.querySelectorAll('.ptile').length") == n0, "the restored photo is back in Photos")
if PH: zoom(1)
print("FAILS", fails)
