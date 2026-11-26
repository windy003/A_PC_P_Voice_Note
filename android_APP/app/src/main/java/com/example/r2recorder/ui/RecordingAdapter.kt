package com.example.r2recorder.ui

import android.content.Context
import android.content.res.ColorStateList
import android.view.LayoutInflater
import android.view.ViewGroup
import androidx.core.content.ContextCompat
import androidx.core.view.isVisible
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import com.example.r2recorder.R
import com.example.r2recorder.data.Recording
import com.example.r2recorder.data.UploadStatus
import com.example.r2recorder.databinding.ItemRecordingBinding
import com.example.r2recorder.util.Formats

class RecordingAdapter(
    private val callbacks: Callbacks
) : ListAdapter<Recording, RecordingAdapter.Holder>(Diff) {

    interface Callbacks {
        fun onPlay(recording: Recording)
        fun onOpen(recording: Recording)
        fun onRetry(recording: Recording)
        fun onDelete(recording: Recording)
    }

    private var playingId: String? = null

    fun setPlayingId(id: String?) {
        if (playingId == id) return
        playingId = id
        notifyItemRangeChanged(0, itemCount)
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): Holder {
        val binding = ItemRecordingBinding.inflate(LayoutInflater.from(parent.context), parent, false)
        return Holder(binding)
    }

    override fun onBindViewHolder(holder: Holder, position: Int) {
        holder.bind(getItem(position))
    }

    inner class Holder(private val binding: ItemRecordingBinding) :
        RecyclerView.ViewHolder(binding.root) {

        fun bind(item: Recording) {
            val context = binding.root.context
            val fileExists = item.localFile(context).exists()
            val isPlaying = playingId == item.id

            binding.tvName.text = Formats.displayName(item.createdAt)
            binding.tvMeta.text = context.getString(
                R.string.recording_meta,
                Formats.dateTime(item.createdAt),
                Formats.duration(item.durationMs),
                Formats.size(item.sizeBytes),
                item.fileName
            )

            val (statusText, statusColor) = when (item.status) {
                UploadStatus.LOCAL -> R.string.status_local to R.color.status_local
                UploadStatus.UPLOADING -> R.string.status_uploading to R.color.status_uploading
                UploadStatus.UPLOADED -> R.string.status_uploaded to R.color.status_uploaded
            }
            binding.tvStatus.setText(statusText)
            binding.tvStatus.backgroundTintList =
                ColorStateList.valueOf(ContextCompat.getColor(context, statusColor))

            binding.tvError.isVisible = !item.error.isNullOrBlank()
            binding.tvError.text = item.error

            binding.btnPlay.isEnabled = fileExists
            binding.btnPlay.text = context.getString(
                if (isPlaying) R.string.action_stop_play else R.string.action_play
            )
            binding.btnOpen.isVisible = !item.remoteUrl.isNullOrBlank()
            binding.btnRetry.isVisible = item.status == UploadStatus.LOCAL
            binding.btnRetry.text = context.getString(R.string.action_upload)

            binding.btnPlay.setOnClickListener { callbacks.onPlay(item) }
            binding.btnOpen.setOnClickListener { callbacks.onOpen(item) }
            binding.btnRetry.setOnClickListener { callbacks.onRetry(item) }
            binding.btnDelete.setOnClickListener { callbacks.onDelete(item) }
        }
    }

    private object Diff : DiffUtil.ItemCallback<Recording>() {
        override fun areItemsTheSame(oldItem: Recording, newItem: Recording): Boolean =
            oldItem.id == newItem.id

        override fun areContentsTheSame(oldItem: Recording, newItem: Recording): Boolean =
            oldItem == newItem
    }
}
