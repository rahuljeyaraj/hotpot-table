#include "ofMain.h"
#include "ofApp.h"
#include "ofAppGLFWWindow.h"
#include <GLFW/glfw3.h>

#ifdef _WIN32
#include <windows.h>
#endif

// Which monitor to project onto, resolved by desktop ORIGIN rather than by
// GLFW monitor index.
//
// The index is not stable: two consecutive restarts with the same three
// physical monitors and the same cabling can produce different
// index-to-position mappings, which silently makes the projector "whichever
// monitor GLFW enumerates second right now" and lands the app on the wrong
// screen. Each physical monitor's desktop origin, by contrast, stays put
// across restarts — Windows ties it to the port/EDID, not to enumeration
// order.
//
// display.txt holds "x,y", the target monitor's desktop origin. A bare
// integer with no comma is still accepted as a one-time legacy index lookup,
// logged loudly since it is the fragile mode this replaces, so an old file
// does not silently misbehave; a fresh file is always written as "x,y".
struct MonitorTarget {
	bool hasOrigin = false;
	int x = 0, y = 0;
	int legacyIndex = -1;
};

//--------------------------------------------------------------
MonitorTarget readMonitorTarget(){
	std::string path = "display.txt";
	MonitorTarget target;

	if(!ofFile::doesFileExist(path)){
		// First-ever run on this machine: nothing to target yet. Legacy
		// index 0 (falls back to whatever GLFW calls monitor 0) is the only
		// sane default with zero prior information.
		ofFile out(path, ofFile::WriteOnly);
		out << "0";
		out.close();
		target.legacyIndex = 0;
		return target;
	}

	ofBuffer buffer = ofBufferFromFile(path);
	std::string line = *buffer.getLines().begin();
	auto comma = line.find(',');
	if(comma == std::string::npos){
		target.legacyIndex = ofToInt(line);
	}
	else {
		target.hasOrigin = true;
		target.x = ofToInt(line.substr(0, comma));
		target.y = ofToInt(line.substr(comma + 1));
	}
	return target;
}

//--------------------------------------------------------------
// Logs every monitor and returns the desktop origin of the one matching
// `target` — by exact origin when display.txt gives one, by legacy index
// otherwise. Rewrites display.txt to the resolved monitor's own origin
// either way, so a legacy-index file self-upgrades to the stable form the
// first time it is actually used, and a later hotplug that changes which
// index maps to that origin no longer matters.
glm::ivec2 logMonitors(const MonitorTarget & target, int & chosenIndex){
	glfwInit();

	int count = 0;
	GLFWmonitor ** monitors = glfwGetMonitors(&count);

	ofLogNotice("main") << "Detected " << count << " monitor(s):";
	for(int i = 0; i < count; i++){
		const GLFWvidmode * mode = glfwGetVideoMode(monitors[i]);
		int x, y;
		glfwGetMonitorPos(monitors[i], &x, &y);
		const char * name = glfwGetMonitorName(monitors[i]);

		ofLogNotice("main") << "  [" << i << "] " << name
			<< " " << mode->width << "x" << mode->height
			<< " @ " << mode->refreshRate << "Hz"
			<< " pos(" << x << "," << y << ")";
	}

	if(count < 1){
		ofLogError("main") << "No monitors reported by GLFW";
		return glm::ivec2(0, 0);
	}

	int chosen = -1;
	if(target.hasOrigin){
		for(int i = 0; i < count; i++){
			int x, y;
			glfwGetMonitorPos(monitors[i], &x, &y);
			if(x == target.x && y == target.y){
				chosen = i;
				break;
			}
		}
		if(chosen < 0){
			ofLogWarning("main") << "display.txt asks for the monitor at origin("
				<< target.x << "," << target.y << ") but no currently attached "
				<< "monitor sits there — falling back to 0. Re-check which "
				<< "physical monitor is the projector and rewrite display.txt "
				<< "as \"x,y\" from the log above.";
			chosen = 0;
		}
	}
	else {
		int idx = target.legacyIndex;
		ofLogWarning("main") << "display.txt is in the OLD index-based format ("
			<< idx << ") — this is the fragile mode that put the app on the "
			<< "wrong monitor last time. Resolving it once by index, then "
			<< "rewriting the file to the resolved origin so this does not "
			<< "happen again.";
		if(idx < 0 || idx >= count){
			ofLogWarning("main") << "  index " << idx << " but only " << count
				<< " present - falling back to 0";
			idx = 0;
		}
		chosen = idx;
	}

	int ox = 0, oy = 0;
	glfwGetMonitorPos(monitors[chosen], &ox, &oy);

	ofLogNotice("main") << "Using monitor [" << chosen << "] origin("
		<< ox << "," << oy << ")";

	ofFile out("display.txt", ofFile::WriteOnly);
	out << ox << "," << oy;
	out.close();

	chosenIndex = chosen;
	return glm::ivec2(ox, oy);
}

//========================================================================
int main( ){

#ifdef _WIN32
	// No manifest declares this app DPI-aware, so without this Windows
	// virtualizes it against a system-wide DPI baseline — usually the primary
	// monitor's scale — instead of the monitor the fullscreen window actually
	// lands on. The symptom is mouse coordinates and the render surface being
	// scaled against the wrong monitor: content reaches only a fraction of the
	// screen, and less of it the higher the target monitor's DPI. Must run
	// before any window is created.
	SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
#endif

	// Set explicitly: the default channel's lazy initialiser never runs on
	// this toolchain, so without this nothing is logged at all.
	ofSetLoggerChannel(std::make_shared<ofConsoleLoggerChannel>());

	MonitorTarget target = readMonitorTarget();
	int monitorIndex = 0;
	glm::ivec2 monitorOrigin = logMonitors(target, monitorIndex);

	// oF creates a plain windowed window first and defers setFullscreen() to the
	// first frame, where the target monitor is resolved from wherever that window
	// landed (ofAppGLFWWindow::getCurrentMonitor). So place the window on the
	// target monitor ourselves and go fullscreen from ofApp::setup().
	// Inset from the origin: Windows shifts a decorated window a few px off the
	// requested spot (asked for the exact origin, got origin + (-1,-7)), which puts
	// the top-left just OUTSIDE the target monitor's rect. getCurrentMonitor()
	// point-tests that corner and falls back to monitor 0 when it misses.
	const int kCornerInset = 100;

	ofGLFWWindowSettings settings;
	settings.setSize(1920, 1080);
	settings.windowMode = OF_WINDOW;
	settings.monitor = monitorIndex;
	settings.setPosition(ofVec2f(monitorOrigin.x + kCornerInset, monitorOrigin.y + kCornerInset));

	auto window = ofCreateWindow(settings);

	ofLogNotice("main") << "After create: pos("
		<< window->getWindowPosition().x << "," << window->getWindowPosition().y
		<< ") size " << window->getWindowSize().x << "x" << window->getWindowSize().y;

	ofRunApp(window, std::make_shared<ofApp>());
	ofRunMainLoop();

}
