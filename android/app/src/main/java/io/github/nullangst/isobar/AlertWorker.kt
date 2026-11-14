package io.github.nullangst.isobar

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import com.chaquo.python.Python
import org.json.JSONArray
import java.util.concurrent.TimeUnit

/**
 * Checks for new weather alerts in the background, about every 15 minutes
 * (the shortest interval Android allows for periodic work), and posts them as
 * notifications. The rules for what notifies live in Python (isobar/notify.py),
 * shared with the desktop tray, so both platforms behave the same.
 */
class AlertWorker(context: Context, params: WorkerParameters) : Worker(context, params) {

    override fun doWork(): Result {
        val json = try {
            Python.getInstance()
                .getModule("isobar.android")
                .callAttr("check_alerts", applicationContext.filesDir.absolutePath, applicationContext.cacheDir.absolutePath)
                .toString()
        } catch (e: Throwable) {
            Log.w(TAG, "alert check failed", e)
            return Result.retry()
        }
        val notices = try {
            JSONArray(json)
        } catch (e: Exception) {
            Log.w(TAG, "bad alert check result", e)
            return Result.success()
        }
        for (i in 0 until notices.length()) {
            val n = notices.getJSONObject(i)
            post(applicationContext, n.optString("title"), n.optString("body"), n.optString("channel", CHANNEL_ALERTS))
        }
        return Result.success()
    }

    companion object {
        private const val TAG = "Isobar"
        private const val WORK_NAME = "isobar-alerts"
        const val CHANNEL_CRITICAL = "critical"
        const val CHANNEL_ALERTS = "alerts"
        const val CHANNEL_UPDATES = "updates"
        const val EXTRA_VIEW = "io.github.nullangst.isobar.VIEW"

        /** Starts the periodic check. Safe to call on every launch. */
        fun schedule(context: Context) {
            createChannels(context)
            val request = PeriodicWorkRequestBuilder<AlertWorker>(15, TimeUnit.MINUTES)
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .build()
            WorkManager.getInstance(context)
                .enqueueUniquePeriodicWork(WORK_NAME, ExistingPeriodicWorkPolicy.KEEP, request)
        }

        fun createChannels(context: Context) {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
            val manager = context.getSystemService(NotificationManager::class.java) ?: return
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL_CRITICAL, "Tornado warnings and emergencies", NotificationManager.IMPORTANCE_HIGH).apply {
                    description = "Tornado and extreme wind warnings, emergencies and PDS warnings"
                    enableVibration(true)
                    vibrationPattern = longArrayOf(0, 600, 300, 600, 300, 600)
                }
            )
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL_ALERTS, "Warnings", NotificationManager.IMPORTANCE_HIGH).apply {
                    description = "Other warnings for your places"
                }
            )
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL_UPDATES, "Watches and other alerts", NotificationManager.IMPORTANCE_DEFAULT).apply {
                    description = "Watches, advisories, SPC mesoscale discussions and service notices"
                }
            )
        }

        fun post(context: Context, title: String, body: String, channel: String) {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
                ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
            ) {
                Log.i(TAG, "notification permission not granted; skipped: $title")
                return
            }
            val open = Intent(context, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
                putExtra(EXTRA_VIEW, "alerts")
            }
            val pending = PendingIntent.getActivity(
                context, 0, open, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
            val critical = channel == CHANNEL_CRITICAL
            val notification = NotificationCompat.Builder(context, channel)
                .setSmallIcon(R.drawable.ic_notify)
                .setContentTitle(title)
                .setContentText(body)
                .setStyle(NotificationCompat.BigTextStyle().bigText(body))
                .setContentIntent(pending)
                .setAutoCancel(true)
                .setCategory(if (critical) NotificationCompat.CATEGORY_ALARM else NotificationCompat.CATEGORY_STATUS)
                .setPriority(if (channel == CHANNEL_UPDATES) NotificationCompat.PRIORITY_DEFAULT else NotificationCompat.PRIORITY_HIGH)
                .build()
            try {
                NotificationManagerCompat.from(context).notify((title + body).hashCode(), notification)
            } catch (e: SecurityException) {
                Log.w(TAG, "could not post notification", e)
            }
        }
    }
}
