package io.github.nullangst.isobar

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.res.Configuration
import android.graphics.Color
import android.os.Bundle
import android.util.Log
import android.view.View
import android.widget.RemoteViews
import androidx.work.Constraints
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import com.chaquo.python.Python
import org.json.JSONObject

/**
 * Home screen widgets. Python (isobar/widget.py) decides what they say and in
 * which colors, and hands over a small JSON object. This file only draws it.
 *
 * The last JSON is kept in SharedPreferences, so Android can redraw a widget
 * (after a reboot, a resize, a launcher restart) without starting Python.
 *
 * Widgets get fresh data from three places:
 * - AlertWorker, every 15 minutes, right after its alert check
 * - the app, each time it loads the forecast or a widget setting changes
 * - Android itself, every 30 minutes at most (updatePeriodMillis)
 */
object Widgets {
    private const val TAG = "Isobar"
    private const val PREFS = "isobar_widget"
    private const val KEY_JSON = "json"

    // The wide widget's hours row only fits from about this tall up.
    private const val HOURS_MIN_HEIGHT_DP = 140
    // Below this the small widget drops the condition and alert lines (2x1).
    private const val SMALL_COMPACT_HEIGHT_DP = 100

    private val hourIds = listOf(
        Triple(R.id.h0_time, R.id.h0_icon, R.id.h0_temp),
        Triple(R.id.h1_time, R.id.h1_icon, R.id.h1_temp),
        Triple(R.id.h2_time, R.id.h2_icon, R.id.h2_temp),
        Triple(R.id.h3_time, R.id.h3_icon, R.id.h3_temp),
        Triple(R.id.h4_time, R.id.h4_icon, R.id.h4_temp),
    )

    private fun ids(context: Context, cls: Class<*>): IntArray =
        AppWidgetManager.getInstance(context).getAppWidgetIds(ComponentName(context, cls))

    fun hasAny(context: Context): Boolean =
        ids(context, SmallWidget::class.java).isNotEmpty() || ids(context, WideWidget::class.java).isNotEmpty()

    /** Ask Python for fresh data and redraw. Blocks, so only call it off the main thread. */
    fun refreshNow(context: Context) {
        if (!hasAny(context)) return
        val json = try {
            Python.getInstance()
                .getModule("isobar.android")
                .callAttr("widget_data", context.filesDir.absolutePath, context.cacheDir.absolutePath)
                .toString()
        } catch (e: Throwable) {
            Log.w(TAG, "widget data failed", e)
            return
        }
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(KEY_JSON, json).apply()
        drawAll(context)
    }

    /** Redraw every widget from the last data, without Python. */
    fun drawAll(context: Context) {
        val manager = AppWidgetManager.getInstance(context)
        val data = load(context)
        for (id in ids(context, SmallWidget::class.java)) manager.updateAppWidget(id, draw(context, manager, id, false, data))
        for (id in ids(context, WideWidget::class.java)) manager.updateAppWidget(id, draw(context, manager, id, true, data))
    }

    private fun load(context: Context): JSONObject? {
        val raw = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY_JSON, null) ?: return null
        return try {
            JSONObject(raw)
        } catch (e: Exception) {
            null
        }
    }

    private fun icon(name: String?): Int = when (name) {
        "clear_day" -> R.drawable.wx_clear_day
        "clear_night" -> R.drawable.wx_clear_night
        "partly_day" -> R.drawable.wx_partly_day
        "partly_night" -> R.drawable.wx_partly_night
        "fog" -> R.drawable.wx_fog
        "rain" -> R.drawable.wx_rain
        "snow" -> R.drawable.wx_snow
        "sleet" -> R.drawable.wx_sleet
        "thunder" -> R.drawable.wx_thunder
        else -> R.drawable.wx_cloudy
    }

    /** The palette for this phone: the fixed one, or light or dark by the phone's dark mode. */
    private fun palette(context: Context, data: JSONObject?): JSONObject? {
        val colors = data?.optJSONObject("colors") ?: return null
        colors.optJSONObject("fixed")?.let { return it }
        val night = (context.resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK) ==
            Configuration.UI_MODE_NIGHT_YES
        return colors.optJSONObject(if (night) "dark" else "light")
    }

    private fun color(palette: JSONObject?, key: String, fallback: String): Int = try {
        Color.parseColor(palette?.optString(key)?.takeIf { it.isNotEmpty() } ?: fallback)
    } catch (e: IllegalArgumentException) {
        Color.parseColor(fallback)
    }

    private fun openApp(context: Context, view: String?, requestCode: Int): PendingIntent {
        val intent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
            if (view != null) putExtra(AlertWorker.EXTRA_VIEW, view)
        }
        return PendingIntent.getActivity(
            context, requestCode, intent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
    }

    private fun text(views: RemoteViews, id: Int, value: String?) {
        if (value.isNullOrEmpty()) {
            views.setViewVisibility(id, View.GONE)
        } else {
            views.setTextViewText(id, value)
            views.setViewVisibility(id, View.VISIBLE)
        }
    }

    private fun draw(context: Context, manager: AppWidgetManager, id: Int, wide: Boolean, data: JSONObject?): RemoteViews {
        val views = RemoteViews(context.packageName, if (wide) R.layout.widget_wide else R.layout.widget_small)
        val options: Bundle = manager.getAppWidgetOptions(id)
        val height = options.getInt(AppWidgetManager.OPTION_APPWIDGET_MIN_HEIGHT, 110)

        val p = palette(context, data)
        val bg = color(p, "bg", "#1C232B")
        val ink = color(p, "ink", "#E8EDF2")
        val ink2 = color(p, "ink2", "#A9B4C0")
        val accent = color(p, "accent", "#8FC3FF")
        val warn = color(p, "warn", "#EF5B5B")
        val watch = color(p, "watch", "#E8C547")

        views.setInt(R.id.widget_bg, "setColorFilter", bg)
        views.setInt(R.id.widget_bg, "setImageAlpha", data?.optInt("alpha", 255) ?: 255)
        views.setOnClickPendingIntent(R.id.widget_root, openApp(context, null, 2))

        if (data == null) {
            views.setTextViewText(R.id.temp, "--")
            text(views, R.id.cond, "Open Isobar to set up")
            views.setTextColor(R.id.temp, ink)
            views.setTextColor(R.id.cond, ink2)
            return views
        }

        val note = data.optString("note")
        views.setTextViewText(R.id.temp, data.optString("temp", "--"))
        views.setImageViewResource(R.id.wx_icon, icon(data.optString("icon")))
        text(views, R.id.place, data.optString("place"))
        for (tv in listOf(R.id.temp, R.id.cond)) views.setTextColor(tv, ink)
        views.setTextColor(R.id.place, ink2)

        val alert = data.optJSONObject("alert")
        val level = alert?.optString("level") ?: ""
        val alertColor = when (level) {
            "critical", "warning" -> warn
            "watch" -> watch
            else -> accent
        }
        val alertText = alert?.let {
            val more = it.optInt("more", 0)
            listOf(it.optString("text"), it.optString("until"), if (more > 0) "+$more more" else "")
                .filter { s -> s.isNotEmpty() }.joinToString(", ")
        }

        if (wide) {
            text(views, R.id.cond, data.optString("cond").ifEmpty { note })
            val details = data.optBoolean("details", true)
            text(views, R.id.hilo, if (details) data.optString("hilo") else null)
            text(views, R.id.feels, if (details) data.optString("feels") else null)
            text(views, R.id.rain, if (details) data.optString("rain") else null)
            text(views, R.id.updated, data.optString("updated").let { if (it.isEmpty()) "" else "Updated $it" })
            for (tv in listOf(R.id.hilo, R.id.feels, R.id.updated)) views.setTextColor(tv, ink2)
            views.setTextColor(R.id.rain, accent)

            if (alertText.isNullOrEmpty()) {
                views.setViewVisibility(R.id.alert_row, View.GONE)
            } else {
                views.setViewVisibility(R.id.alert_row, View.VISIBLE)
                views.setTextViewText(R.id.alert, alertText)
                views.setTextColor(R.id.alert, ink)
                views.setInt(R.id.alert_bg, "setColorFilter", alertColor)
                views.setInt(R.id.alert_bg, "setImageAlpha", 70)
                views.setOnClickPendingIntent(R.id.alert_row, openApp(context, "alerts", 1))
            }

            val hours = data.optJSONArray("hours")
            val showHours = data.optBoolean("show_hours", true) && height >= HOURS_MIN_HEIGHT_DP &&
                hours != null && hours.length() > 0
            views.setViewVisibility(R.id.hours_row, if (showHours) View.VISIBLE else View.GONE)
            if (showHours && hours != null) {
                hourIds.forEachIndexed { i, (timeId, iconId, tempId) ->
                    val h = hours.optJSONObject(i)
                    if (h == null) {
                        views.setViewVisibility(timeId, View.INVISIBLE)
                        views.setViewVisibility(iconId, View.INVISIBLE)
                        views.setViewVisibility(tempId, View.INVISIBLE)
                    } else {
                        for (v in listOf(timeId, iconId, tempId)) views.setViewVisibility(v, View.VISIBLE)
                        views.setTextViewText(timeId, h.optString("time"))
                        views.setImageViewResource(iconId, icon(h.optString("icon")))
                        views.setTextViewText(tempId, h.optString("temp"))
                        views.setTextColor(timeId, ink2)
                        views.setTextColor(tempId, ink)
                    }
                }
            }
            val showNote = note.isNotEmpty() && data.optString("cond").isNotEmpty()
            text(views, R.id.note, if (showNote) note else null)
            views.setTextColor(R.id.note, ink2)
        } else {
            val compact = height < SMALL_COMPACT_HEIGHT_DP
            text(views, R.id.cond, if (compact) null else data.optString("cond").ifEmpty { note })
            text(views, R.id.alert, if (compact) null else alertText)
            views.setTextColor(R.id.alert, alertColor)
            if (!alertText.isNullOrEmpty()) views.setOnClickPendingIntent(R.id.alert, openApp(context, "alerts", 1))
        }
        return views
    }
}

/** Isobar, 2x2: temperature, conditions, place and the top alert. */
class SmallWidget : AppWidgetProvider() {
    override fun onUpdate(context: Context, manager: AppWidgetManager, ids: IntArray) {
        Widgets.drawAll(context)
        WidgetWorker.enqueue(context)
    }

    override fun onAppWidgetOptionsChanged(context: Context, manager: AppWidgetManager, id: Int, options: Bundle) {
        Widgets.drawAll(context)
    }

    override fun onEnabled(context: Context) {
        // Keeps widgets current even if the app itself is never opened again.
        AlertWorker.schedule(context)
    }
}

/** Isobar, 4x2: everything the small one has, plus details and the next five hours. */
class WideWidget : AppWidgetProvider() {
    override fun onUpdate(context: Context, manager: AppWidgetManager, ids: IntArray) {
        Widgets.drawAll(context)
        WidgetWorker.enqueue(context)
    }

    override fun onAppWidgetOptionsChanged(context: Context, manager: AppWidgetManager, id: Int, options: Bundle) {
        Widgets.drawAll(context)
    }

    override fun onEnabled(context: Context) {
        AlertWorker.schedule(context)
    }
}

/** One widget refresh off the main thread. Repeated requests collapse into one. */
class WidgetWorker(context: Context, params: WorkerParameters) : Worker(context, params) {
    override fun doWork(): Result {
        Widgets.refreshNow(applicationContext)
        return Result.success()
    }

    companion object {
        private const val WORK_NAME = "isobar-widgets"

        fun enqueue(context: Context) {
            if (!Widgets.hasAny(context)) return
            val request = OneTimeWorkRequestBuilder<WidgetWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .build()
            WorkManager.getInstance(context).enqueueUniqueWork(WORK_NAME, ExistingWorkPolicy.REPLACE, request)
        }
    }
}
