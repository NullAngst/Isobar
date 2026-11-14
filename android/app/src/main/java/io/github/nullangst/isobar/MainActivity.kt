package io.github.nullangst.isobar

import android.Manifest
import android.annotation.SuppressLint
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.CancellationSignal
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.util.Log
import android.view.RoundedCorner
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.webkit.ConsoleMessage
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import com.chaquo.python.Python
import kotlin.concurrent.thread
import kotlin.math.max
import kotlin.math.sqrt

/**
 * The whole Android app is this one screen: a WebView showing the same UI as the
 * desktop app, served by the same Python server running inside the app
 * (isobar/android.py, started through Chaquopy).
 */
class MainActivity : AppCompatActivity() {

    private lateinit var frame: FrameLayout
    private lateinit var web: WebView

    // How far the bottom bar has to move in (sides) and up, in CSS pixels, to
    // stay clear of a rounded screen corner. Sent to the page as CSS variables.
    private var cornerX = 0f
    private var cornerY = 0f

    // Port of Isobar's own server, once it's running. Only that origin loads in the WebView.
    private var serverPort = -1

    // A view to open once the page has loaded (from a notification tap).
    private var pendingView: String? = null
    private var pageLoaded = false

    private val notifyPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (!granted) Log.i(TAG, "notifications not allowed; alerts will only show in the app")
    }

    private val locationPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) locate() else sendLocation(null, "Location permission denied")
    }

    @SuppressLint("SetJavaScriptEnabled", "JavascriptInterface")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // Draw edge to edge and pad the content ourselves, so the page never sits
        // under the status bar, the navigation bar, a camera cutout or the keyboard.
        WindowCompat.setDecorFitsSystemWindows(window, false)
        frame = FrameLayout(this)
        web = WebView(this)
        frame.addView(web, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))
        setContentView(frame)
        ViewCompat.setOnApplyWindowInsetsListener(frame) { view, insets ->
            val bars = insets.getInsets(
                WindowInsetsCompat.Type.systemBars() or
                    WindowInsetsCompat.Type.displayCutout() or
                    WindowInsetsCompat.Type.ime()
            )
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            measureCorners(view, bars.bottom, max(bars.left, bars.right))
            WindowInsetsCompat.CONSUMED
        }

        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            allowFileAccess = false
            allowContentAccess = false
            setSupportZoom(false)
            builtInZoomControls = false
            displayZoomControls = false
        }
        // Transparent until the page paints, so the window background shows instead of white.
        web.setBackgroundColor(Color.TRANSPARENT)
        web.addJavascriptInterface(Bridge(), "IsobarAndroid")

        web.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView, url: String?) {
                pushCorners()
                pageLoaded = true
                openPendingView()
            }

            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                // Only Isobar's own pages load in here. Anything else goes to the browser.
                val url = request.url
                if (url.scheme == "http" && url.host == "127.0.0.1" && url.port == serverPort) return false
                openExternally(request.url)
                return true
            }
        }
        web.webChromeClient = object : WebChromeClient() {
            override fun onConsoleMessage(message: ConsoleMessage): Boolean {
                Log.d(TAG, "js: ${message.message()} (${message.sourceId()}:${message.lineNumber()})")
                return true
            }
        }

        // Back closes whatever is open in the UI first (settings, search, a map panel),
        // then returns to Now, and only then leaves the app.
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                web.evaluateJavascript(
                    "(window.isobar && window.isobar.back) ? window.isobar.back() : false"
                ) { result ->
                    // Nothing left to close: send the app to the background, which is
                    // what back does on the home screen of any app since Android 12.
                    if (result != "true") moveTaskToBack(true)
                }
            }
        })

        pendingView = intent?.getStringExtra(AlertWorker.EXTRA_VIEW)
        start()
        AlertWorker.schedule(this)
        askForNotifications()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        intent.getStringExtra(AlertWorker.EXTRA_VIEW)?.let {
            pendingView = it
            if (pageLoaded) openPendingView()
        }
    }

    private fun openPendingView() {
        val view = pendingView ?: return
        pendingView = null
        // Only known view names go into the script.
        if (view !in setOf("now", "forecast", "radar", "outlooks", "alerts", "discussion")) return
        web.evaluateJavascript("window.isobar && window.isobar.go('$view')", null)
    }

    /** Asks once, on Android 13 and later, for permission to post alert notifications. */
    private fun askForNotifications() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED) return
        val prefs = getSharedPreferences("isobar", Context.MODE_PRIVATE)
        if (prefs.getBoolean("asked_notifications", false)) return
        prefs.edit().putBoolean("asked_notifications", true).apply()
        notifyPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
    }

    // ------------------------------------------------------------ location

    private fun requestLocation() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED) {
            locate()
        } else {
            locationPermission.launch(Manifest.permission.ACCESS_COARSE_LOCATION)
        }
    }

    /** One coarse fix (about a kilometer), which is all a forecast needs. No Google services. */
    @SuppressLint("MissingPermission")
    private fun locate() {
        val manager = getSystemService(LocationManager::class.java)
        if (manager == null) {
            sendLocation(null, "No location service on this device")
            return
        }
        val provider = when {
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.S && manager.isProviderEnabled(LocationManager.FUSED_PROVIDER) -> LocationManager.FUSED_PROVIDER
            manager.isProviderEnabled(LocationManager.NETWORK_PROVIDER) -> LocationManager.NETWORK_PROVIDER
            manager.isProviderEnabled(LocationManager.PASSIVE_PROVIDER) -> LocationManager.PASSIVE_PROVIDER
            else -> null
        }
        if (provider == null) {
            sendLocation(null, "Location is turned off")
            return
        }
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                manager.getCurrentLocation(provider, CancellationSignal(), mainExecutor) { location ->
                    sendLocation(location ?: manager.getLastKnownLocation(provider), "No fix")
                }
            } else {
                // Every method spelled out: before Android 11 the system calls all
                // four, and a lambda would only supply the first.
                val listener = object : LocationListener {
                    override fun onLocationChanged(location: Location) = sendLocation(location, null)
                    @Deprecated("Deprecated in Java")
                    override fun onStatusChanged(provider: String?, status: Int, extras: Bundle?) {}
                    override fun onProviderEnabled(provider: String) {}
                    override fun onProviderDisabled(provider: String) {}
                }
                @Suppress("DEPRECATION")
                manager.requestSingleUpdate(provider, listener, Looper.getMainLooper())
                Handler(Looper.getMainLooper()).postDelayed({
                    // Some devices never answer requestSingleUpdate indoors.
                    sendLocation(manager.getLastKnownLocation(provider), "No fix")
                }, 20000)
            }
        } catch (e: Exception) {
            Log.w(TAG, "location failed", e)
            sendLocation(null, "Location failed")
        }
    }

    private fun sendLocation(location: Location?, error: String?) {
        runOnUiThread {
            if (!::web.isInitialized) return@runOnUiThread
            val js = if (location != null) {
                "window.isobar && window.isobar.onLocation(${location.latitude}, ${location.longitude}, null)"
            } else {
                // The message is one of the fixed strings above, never outside text.
                "window.isobar && window.isobar.onLocation(NaN, NaN, '${error ?: "No fix"}')"
            }
            web.evaluateJavascript(js, null)
        }
    }

    private fun start() {
        // Python is already running (PyApplication starts it). Starting the server
        // is quick, but it reads files and binds a port, so keep it off the UI thread.
        thread(name = "isobar-start") {
            val url = try {
                Python.getInstance()
                    .getModule("isobar.android")
                    .callAttr("start", filesDir.absolutePath, cacheDir.absolutePath)
                    .toString()
            } catch (e: Throwable) {
                Log.e(TAG, "could not start the local server", e)
                null
            }
            runOnUiThread {
                if (isDestroyed) return@runOnUiThread
                if (url != null) {
                    serverPort = Uri.parse(url).port
                    web.loadUrl(url)
                } else {
                    web.loadDataWithBaseURL(null, ERROR_PAGE, "text/html", "utf-8", null)
                }
            }
        }
    }

    /**
     * With three-button navigation the bar at the bottom of the screen already
     * keeps content clear of the rounded corners. With gesture navigation that
     * bar is only a thin strip, so the corners of the screen cut into the ends of
     * Isobar's bottom bar. This works out how far to pull it in and up.
     */
    private fun measureCorners(view: View, bottomInset: Int, sideInset: Int) {
        var x = 0f
        var y = 0f
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val raw = view.rootWindowInsets
            val radius = listOfNotNull(
                raw?.getRoundedCorner(RoundedCorner.POSITION_BOTTOM_LEFT),
                raw?.getRoundedCorner(RoundedCorner.POSITION_BOTTOM_RIGHT),
            ).maxOfOrNull { it.radius } ?: 0
            if (radius > 0) {
                val r = radius.toFloat()
                // Lift the bar until it clears the steepest part of the curve...
                val lift = max(0f, 0.3f * r - bottomInset)
                val above = bottomInset + lift
                // ...then pull its ends in by however much curve is left at that height.
                val side = if (above >= r) 0f else r - sqrt(r * r - (r - above) * (r - above))
                val density = resources.displayMetrics.density
                x = max(0f, side - sideInset) / density
                y = lift / density
                if (x > 0f) x += 4f // a little breathing room past the curve
            }
        }
        if (x != cornerX || y != cornerY) {
            cornerX = x
            cornerY = y
            pushCorners()
        }
    }

    private fun pushCorners() {
        if (!::web.isInitialized) return
        val js = "document.documentElement.style.setProperty('--corner-x','${cornerX}px');" +
            "document.documentElement.style.setProperty('--corner-y','${cornerY}px');"
        web.evaluateJavascript(js, null)
    }

    private fun openExternally(uri: Uri) {
        // Web links only. Anything else (intent:, file:, content:, custom schemes) is dropped.
        if (uri.scheme != "https" && uri.scheme != "http") {
            Log.w(TAG, "refused to open ${uri.scheme} link")
            return
        }
        try {
            startActivity(Intent(Intent.ACTION_VIEW, uri))
        } catch (e: ActivityNotFoundException) {
            Log.w(TAG, "nothing can open $uri")
        }
    }

    /** Called from main.js so the status and navigation bar areas match the app theme. */
    inner class Bridge {
        @JavascriptInterface
        fun requestLocation() {
            runOnUiThread { this@MainActivity.requestLocation() }
        }

        @JavascriptInterface
        fun openNotificationSettings() {
            runOnUiThread {
                val intent = Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                    .putExtra(Settings.EXTRA_APP_PACKAGE, packageName)
                try {
                    startActivity(intent)
                } catch (e: ActivityNotFoundException) {
                    Log.w(TAG, "no notification settings screen")
                }
            }
        }

        @JavascriptInterface
        fun setTheme(background: String, light: Boolean) {
            runOnUiThread {
                val color = try {
                    Color.parseColor(background)
                } catch (e: IllegalArgumentException) {
                    return@runOnUiThread
                }
                frame.setBackgroundColor(color)
                WindowCompat.getInsetsController(window, window.decorView).apply {
                    isAppearanceLightStatusBars = light
                    isAppearanceLightNavigationBars = light
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        web.onResume()
        // Lets the page refresh stale data and follow the phone's location.
        if (pageLoaded) web.evaluateJavascript("window.isobar && window.isobar.resumed && window.isobar.resumed()", null)
    }

    override fun onPause() {
        web.onPause()
        super.onPause()
    }

    override fun onDestroy() {
        web.destroy()
        super.onDestroy()
    }

    companion object {
        private const val TAG = "Isobar"
        private const val ERROR_PAGE = """
            <html><body style="font-family:sans-serif;padding:24px;background:#1c232b;color:#e8edf2">
            <h2>Isobar could not start</h2>
            <p>The built-in server failed to start. Close the app fully and open it again.
            If it keeps happening, the log has details: <code>adb logcat -s Isobar python.stderr</code></p>
            </body></html>
        """
    }
}
