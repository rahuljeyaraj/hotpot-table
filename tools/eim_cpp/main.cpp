// tools/eim_cpp/main.cpp — the whole point of this file is to be a thin,
// boring wrapper: classifier/backend_ei.py (doc section 19.4) shells out to
// the compiled `classify` binary once per bin crop and reads back JSON, the
// same subprocess-call shape capture.py already uses for `v4l2-ctl` rather
// than trusting an inconsistent library binding. Nothing here decides
// anything about the model — that is all vendor/ (the Edge Impulse export)
// and model-parameters/model_metadata.h.
//
// Input: a tiny raw framing this project invents, NOT a JPEG — decoding
// stays in Python (cv2 already does it, doc section 6's frame pipeline
// already hands classifier code a BGR numpy array) so this binary needs no
// image-codec dependency at all:
//
//     int32 width, int32 height, then width*height*3 raw RGB888 bytes
//     (R,G,B per pixel, row-major — NOT BGR; backend_ei.py converts before
//     writing, since EI trained on RGB per doc section 19.2)
//
// **width/height MUST already equal EI_CLASSIFIER_INPUT_WIDTH/HEIGHT
// (160x160 for this model) — checked below, not assumed.** An earlier
// version of this comment claimed the SDK resizes internally from
// whatever size the caller hands it; that was wrong, found by testing
// against real (263x317-ish) bin crops, not by reading a spec: passing
// their native size through as `total_length` reliably segfaulted
// (`get_signal_data` gets asked for offsets that assume the model's own
// pixel count, so a caller providing a different one hands back an
// out-of-bounds read once the DSP layer walks past it). backend_ei.py
// does the resize (squash, matching EI_CLASSIFIER_RESIZE_MODE, doc section
// 19.2) before it ever writes this file.
//
// Output: one line of JSON on stdout,
//     {"labels":[{"label":"button_mushrooms","value":0.83}, ...]}
// in the model's own class order (model-parameters' EI_CLASSIFIER_LABEL_
// COUNT), so the Python side never has to know that order independently —
// reading it out of this process's own output is the one copy of the
// truth. A non-zero exit code with a message on stderr means "no result",
// never a fabricated label.

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>

#include "edge-impulse-sdk/classifier/ei_run_classifier.h"

namespace {

// The buffer get_signal_data() reads from — a callback, not a return
// value, because that is the shape signal_t's `get_data` requires (doc
// comment in numpy_types.h: "Callback function ... should return an int").
std::vector<float> g_features;

int get_signal_data(size_t offset, size_t length, float *out_ptr) {
    if (offset + length > g_features.size()) {
        return -1;  // EIDSP_OUT_OF_BOUNDS-ish; run_classifier surfaces this
    }
    memcpy(out_ptr, g_features.data() + offset, length * sizeof(float));
    return 0;  // EIDSP_OK
}

bool read_raw_rgb(FILE *f, std::vector<unsigned char> *out,
                  int32_t *width, int32_t *height) {
    if (fread(width, sizeof(int32_t), 1, f) != 1) return false;
    if (fread(height, sizeof(int32_t), 1, f) != 1) return false;
    if (*width <= 0 || *height <= 0) return false;
    size_t n = static_cast<size_t>(*width) * static_cast<size_t>(*height) * 3;
    out->resize(n);
    return fread(out->data(), 1, n, f) == n;
}

}  // namespace

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s <raw-rgb-file>\n", argv[0]);
        return 2;
    }

    FILE *f = fopen(argv[1], "rb");
    if (!f) {
        fprintf(stderr, "error: cannot open %s\n", argv[1]);
        return 2;
    }
    std::vector<unsigned char> rgb;
    int32_t width = 0, height = 0;
    bool ok = read_raw_rgb(f, &rgb, &width, &height);
    fclose(f);
    if (!ok) {
        fprintf(stderr, "error: %s is not a well-formed raw-rgb file\n",
                argv[1]);
        return 2;
    }

    size_t npix = static_cast<size_t>(width) * static_cast<size_t>(height);
    if (npix != EI_CLASSIFIER_RAW_SAMPLE_COUNT) {
        fprintf(stderr,
                "error: %s is %dx%d (%zu px) but this model wants exactly "
                "%dx%d (%d px) — resize before writing this file, do not "
                "rely on this binary to do it (see this file's own top "
                "comment)\n",
                argv[1], width, height, npix,
                EI_CLASSIFIER_INPUT_WIDTH, EI_CLASSIFIER_INPUT_HEIGHT,
                EI_CLASSIFIER_RAW_SAMPLE_COUNT);
        return 2;
    }

    // EI's image DSP block wants each pixel packed as a single float
    // holding 0x00RRGGBB.
    g_features.resize(npix);
    for (size_t i = 0; i < npix; i++) {
        uint32_t r = rgb[i * 3 + 0];
        uint32_t g = rgb[i * 3 + 1];
        uint32_t b = rgb[i * 3 + 2];
        g_features[i] = static_cast<float>((r << 16) | (g << 8) | b);
    }

    signal_t signal;
    signal.total_length = npix;
    signal.get_data = &get_signal_data;

    ei_impulse_result_t result;
    memset(&result, 0, sizeof(result));
    EI_IMPULSE_ERROR err = run_classifier(&signal, &result, false);
    if (err != EI_IMPULSE_OK) {
        fprintf(stderr, "error: run_classifier failed (code %d)\n",
                static_cast<int>(err));
        return 1;
    }

    printf("{\"labels\":[");
    for (size_t i = 0; i < EI_CLASSIFIER_LABEL_COUNT; i++) {
        printf("%s{\"label\":\"%s\",\"value\":%.6f}",
               i ? "," : "",
               result.classification[i].label,
               static_cast<double>(result.classification[i].value));
    }
    // No "timing_us" field: clib's ei_read_timer_us() (this binary's
    // porting layer, see CMakeLists.txt) always returns 0 — see that
    // function's own body — so result.timing would be a field that always
    // reads zero, which is worse than not printing it at all.
    printf("]}\n");
    return 0;
}
