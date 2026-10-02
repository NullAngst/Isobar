package io.github.nullangst.isobar

import android.annotation.SuppressLint
import android.content.ActivityNotFoundException
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.util.Log
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.webkit.ConsoleMessage
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import com.chaquo.python.Python
import kotlin.concurrent.thread

/**
 * The whole Android app is this one screen: a WebView showing the same UI as the
 * desktop app, served by the same Python server running inside the app
 * (isobar/android.py, started through Chaquopy).
 */
class MainActivity : AppCompatActivity() {

    private lateinit var frame: FrameLayout
    private lateinit var web: WebView

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
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                // Only Isobar's own pages load in here. Anything else goes to the browser.
                if (request.url.host == "127.0.0.1") return false
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
                    if (result != "true") {
                        isEnabled = false
                        onBackPressedDispatcher.onBackPressed()
                        isEnabled = true
                    }
                }
            }
        })

        start()
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
                    web.loadUrl(url)
                } else {
                    web.loadDataWithBaseURL(null, ERROR_PAGE, "text/html", "utf-8", null)
                }
            }
        }
    }

    private fun openExternally(uri: Uri) {
        try {
            startActivity(Intent(Intent.ACTION_VIEW, uri))
        } catch (e: ActivityNotFoundException) {
            Log.w(TAG, "nothing can open $uri")
        }
    }

    /** Called from main.js so the status and navigation bar areas match the app theme. */
    inner class Bridge {
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
