"""
Station Information Panel for Station 1 Kitting Workflow
Provides detailed explanations of each phase in the actual Station 1 pipeline
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QComboBox, QTextEdit, QGroupBox, QFrame
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QPalette, QColor

class StationInfoPanel(QWidget):
    """Widget that displays detailed information about Station 1 workflow phases"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # Store phase explanations based on ACTUAL Station 1 code
        self.phase_explanations = {
            "Order & Inventory": """📋 **Order & Inventory Phase**
            
**Process Flow:**
1. OrderGenerator creates orders randomly (5% chance per second)
2. Orders enter PLCController.orders_in (SimPy Store)
3. PLCController retrieves order when station_state = IDLE
4. InventoryCheck executes for 1.0 second simulation time
5. 90% success rate for parts availability

**States & Transitions:**
- PLC state: IDLE → CHECKING_INVENTORY
- Inventory state: READY → CHECKING → PARTS_AVAILABLE/PARTS_MISSING
- If parts missing: order returns to queue, 2.0 second delay

**Code References:**
- OrderGenerator.generate_orders() - creates Order objects
- PLCController.orders_in.get() - DES queue retrieval
- InventoryCheck.execute_command("CHECK_INVENTORY") - 1.0s timeout
- Events: ORDER.CREATED, INVENTORY.STATE_CHANGE

**Visual Indicators in 3D:**
- Order box appears at inventory station (-3.0, 0.2, 2.0)
- Station banner shows CHECKING_INVENTORY
- Dashboard: queued_orders count updates
- Order status: QUEUED → PROCESSING""",
            
            "Kitting Phase": """📦 **Kitting Phase - Component Transfer**
            
**Process Flow:**
1. PLC state changes to ROBOTIC_PICKING
2. PickingArm moves: part_bins → staging_area
3. KittingArm moves: staging_area → kitting_tray
4. Each arm performs: move, pick, move, place, return_home
5. 2% failure probability per pick/place operation

**Timings & Operations:**
- Move time: 0.3s base + 0.002s per unit distance
- PickingArm: pick=0.4s, place=0.3s
- KittingArm: pick=0.3s, place=0.4s
- Failure repair: 3.0-8.0 seconds random

**Code References:**
- PLCController.run_arm_pick_sequence("picking", ...)
- PLCController.run_arm_pick_sequence("kitting", ...)
- RoboticArm2Axis.pick_component(), place_component()
- Arm states: IDLE → MOVING → WORKING → IDLE

**Visual Indicators in 3D:**
- PickingArm: grey(IDLE), yellow(MOVING), green(WORKING), red(FAILED)
- Order moves: inventory → staging → kitting_tray (0.0, 0.2, 2.0)
- Arm positions update from work_areas coordinates
- Dashboard: arm.utilization and completed_operations update""",
            
            "Mounting Phase": """🔧 **Mounting Phase - Assembly Operation**
            
**Process Flow:**
1. PLC state changes to ROBOTIC_MOUNTING
2. MountingArm moves: kitting_tray → mounting_area
3. Special operation: mount_assembly() for 1.2 seconds
4. Arm returns to home position
5. 2% failure probability during mount operation

**Mounting Details:**
- Mount time: 1.2 seconds fixed duration
- Pick/place: pick=0.5s, place=0.6s (though not used in this sequence)
- Arm home: (200, 0) in 2D, (1.0, 0.3, -2.0) in 3D
- Work area: mounting_area = (220, 80) in 2D, (1.0, 0.1, 2.0) in 3D

**Code References:**
- PLCController.run_arm_pick_sequence("mounting", ...)
- PLCController.run_arm_special_operation("mounting", "mount")
- RoboticArm2Axis.mount_assembly()
- operation_times["MOUNTING"]["mount"] = 1.2

**Visual Indicators in 3D:**
- MountingArm moves to mounting_area (1.0, 0.1, 2.0)
- Arm shows WORKING state with green pulsing animation
- Order position updates to mounting_area
- Station banner shows ROBOTIC_MOUNTING
- Failure triggers red flashing indicator""",
            
            "Soldering Phase": """🔌 **Soldering Phase - Electrical Connection**
            
**Process Flow:**
1. PLC state changes to ROBOTIC_SOLDERING
2. SolderingArm performs solder_joint() operation
3. Operation time: 1.0 second fixed duration
4. No physical movement required
5. 2% failure probability during solder operation

**Soldering Details:**
- Solder time: 1.0 second fixed duration
- Pick/place times: pick=0.4s, place=0.3s (available but unused)
- Arm home: (300, 0) in 2D, (3.0, 0.3, -2.0) in 3D
- Work area: soldering_area = (320, 90) in 2D, (2.0, 0.1, 2.0) in 3D

**Code References:**
- PLCController.run_arm_special_operation("soldering", "solder")
- RoboticArm2Axis.solder_joint()
- operation_times["SOLDERING"]["solder"] = 1.0
- PLC state: ROBOTIC_MOUNTING → ROBOTIC_SOLDERING

**Visual Indicators in 3D:**
- SolderingArm at soldering_area (2.0, 0.1, 2.0)
- Arm shows WORKING state with green color
- Order position updates to soldering_area
- Station banner shows ROBOTIC_SOLDERING
- Dashboard: soldering_arm.completed_operations increments""",
            
            "Output Handling": """📤 **Output Handling Phase**
            
**Process Flow:**
1. PLC state changes to HANDLING_OUTPUT
2. OutputHandler processes order for 0.5 seconds
3. Order placed into kits_out (SimPy Store)
4. Inventory and Output components reset
5. Station returns to IDLE state

**Output Details:**
- Output handling: 0.5 seconds duration
- OutputHandler.completed_count increments
- Order cycle_time calculated: completion - creation
- Station resets: InventoryCheck and OutputHandler execute "RESET"

**Code References:**
- PLCController.run_output_handling(order)
- OutputHandler.execute_command("HANDLE_OUTPUT")
- PLCController.kits_out.put(order)
- Order.complete(env.now) - sets cycle_time

**Visual Indicators in 3D:**
- Order moves to output_buffer (3.0, 0.1, 2.0)
- Order status: COMPLETED (color brightens)
- Dashboard: completed_count++, wip_count--
- Throughput: (completed_count / sim_time) × 3600
- Cycle times added to statistics for charts"""
        }
        
        # Setup UI
        self.setup_ui()
        
        # Apply styling
        self.apply_styling()
        
        # Set initial explanation
        self.update_explanation("Order & Inventory")
    
    def setup_ui(self):
        """Setup the user interface"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(10)
        
        # Title
        title_label = QLabel("🏭 Station 1 - Kitting Workflow Details")
        title_label.setFont(QFont("Arial", 12, QFont.Bold))
        title_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title_label)
        
        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        main_layout.addWidget(separator)
        
        # Instruction label
        instruction = QLabel("Select workflow phase to view implementation details:")
        instruction.setFont(QFont("Arial", 10))
        instruction.setStyleSheet("color: #cccccc;")
        main_layout.addWidget(instruction)
        
        # Phase selection combo box
        self.phase_combo = QComboBox()
        self.phase_combo.addItems([
            "Order & Inventory",
            "Kitting Phase", 
            "Mounting Phase",
            "Soldering Phase",
            "Output Handling"
        ])
        self.phase_combo.currentTextChanged.connect(self.update_explanation)
        self.phase_combo.setFont(QFont("Arial", 10))
        main_layout.addWidget(self.phase_combo)
        
        # Explanation text area
        explanation_group = QGroupBox("📋 Phase Implementation")
        explanation_group.setFont(QFont("Arial", 10, QFont.Bold))
        explanation_layout = QVBoxLayout(explanation_group)
        
        self.explanation_text = QTextEdit()
        self.explanation_text.setReadOnly(True)
        self.explanation_text.setFont(QFont("Courier", 9))
        self.explanation_text.setMinimumHeight(350)
        self.explanation_text.setMinimumWidth(400)
        
        explanation_layout.addWidget(self.explanation_text)
        main_layout.addWidget(explanation_group)
        
        # Workflow sequence visualization
        sequence_group = QGroupBox("🔄 Station 1 Workflow Sequence")
        sequence_group.setFont(QFont("Arial", 10, QFont.Bold))
        sequence_layout = QVBoxLayout(sequence_group)
        
        sequence_text = QLabel(
            "Order → Inventory Check → Picking → Kitting → Mounting → Soldering → Output"
        )
        sequence_text.setFont(QFont("Arial", 10))
        sequence_text.setAlignment(Qt.AlignCenter)
        sequence_text.setStyleSheet("color: #4CAF50; font-weight: bold;")
        sequence_layout.addWidget(sequence_text)
        
        note = QLabel(
            "Based on actual Station 1 code: PLCController coordinates 4 robotic arms.\n"
            "All timings and failure rates match simulation parameters.\n"
            "Visualization shows real-time state changes and order flow."
        )
        note.setFont(QFont("Arial", 9))
        note.setWordWrap(True)
        note.setStyleSheet("color: #bbbbbb;")
        sequence_layout.addWidget(note)
        
        main_layout.addWidget(sequence_group)
        
        main_layout.addStretch()
    
    def apply_styling(self):
        """Apply dark theme styling to the widget"""
        # Set dark theme palette
        palette = self.palette()
        palette.setColor(QPalette.Window, QColor(45, 45, 48))
        palette.setColor(QPalette.WindowText, Qt.white)
        palette.setColor(QPalette.Base, QColor(30, 30, 30))
        palette.setColor(QPalette.AlternateBase, QColor(45, 45, 48))
        palette.setColor(QPalette.Text, Qt.white)
        palette.setColor(QPalette.Button, QColor(45, 45, 48))
        palette.setColor(QPalette.ButtonText, Qt.white)
        palette.setColor(QPalette.BrightText, Qt.red)
        palette.setColor(QPalette.Link, QColor(42, 130, 218))
        palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
        palette.setColor(QPalette.HighlightedText, Qt.black)
        self.setPalette(palette)
        
        # Style the text edit with monospaced font for code references
        self.explanation_text.setStyleSheet("""
            QTextEdit {
                background-color: #1e1e1e;
                color: #d4d4d4;
                border: 1px solid #3e3e3e;
                border-radius: 4px;
                padding: 8px;
                font-family: 'Consolas', 'Courier New', monospace;
            }
        """)
        
        # Style the combo box
        self.phase_combo.setStyleSheet("""
            QComboBox {
                background-color: #3c3c3c;
                color: white;
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 6px;
                min-height: 24px;
            }
            QComboBox:hover {
                border: 1px solid #777777;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 5px solid white;
                margin-right: 8px;
            }
            QComboBox QAbstractItemView {
                background-color: #3c3c3c;
                color: white;
                selection-background-color: #0078d7;
                border: 1px solid #555555;
            }
        """)
        
        # Style group boxes
        self.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 1px solid #555555;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
                background-color: #2d2d30;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
                color: #ffffff;
            }
        """)
    
    def update_explanation(self, phase_name):
        """Update the explanation text based on selected phase"""
        if phase_name in self.phase_explanations:
            explanation = self.phase_explanations[phase_name]
            
            # Convert markdown-like formatting to HTML
            formatted_text = explanation.replace('\n', '<br>')
            
            # Add HTML styling for better readability
            html_content = f"""
            <div style="font-family: 'Segoe UI', Arial, sans-serif; font-size: 10pt; line-height: 1.4;">
                <div style="color: #4CAF50; font-weight: bold; font-size: 11pt; margin-bottom: 10px;">
                    {phase_name}
                </div>
                <div style="color: #cccccc;">
                    {formatted_text}
                </div>
                <div style="margin-top: 15px; padding: 8px; background-color: #2a2d2e; border-radius: 4px; border-left: 4px solid #0078d7;">
                    <span style="color: #569cd6; font-weight: bold;">📝 Technical Note:</span>
                    <span style="color: #bbbbbb;">
                    These details match the actual Station 1 simulation code. All method names, 
                    state transitions, and timings correspond to the running SimPy simulation.
                    </span>
                </div>
            </div>
            """
            
            self.explanation_text.setHtml(html_content)
        else:
            self.explanation_text.setPlainText(
                f"No information available for {phase_name}"
            )
    
    def get_widget(self):
        """Return the widget itself for embedding in layouts"""
        return self
    
    def get_current_phase(self):
        """Get the currently selected phase name"""
        return self.phase_combo.currentText()
    
    def set_phase(self, phase_name):
        """Programmatically set the current phase"""
        if phase_name in self.phase_explanations:
            self.phase_combo.setCurrentText(phase_name)


# Quick test function for standalone testing
if __name__ == "__main__":
    import sys
    from PyQt5.QtWidgets import QApplication, QMainWindow
    
    class TestWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Station 1 Info Panel - Test")
            self.setGeometry(100, 100, 500, 700)
            
            # Create the panel
            panel = StationInfoPanel()
            self.setCentralWidget(panel)
    
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    
    window = TestWindow()
    window.show()
    
    sys.exit(app.exec_())