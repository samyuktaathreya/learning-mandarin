export default function MultipleChoice({ 
    options, 
    userAnswer, 
    setUserAnswer, 
    hasAnswered, 
    correctAnswer 
}) {
    if (!options || options.length === 0) return null;

    return (
        <div className="mc-container">
            {options.map((option, index) => {
                let className = "mc-option";
                
                if (hasAnswered) {
                    if (option === correctAnswer) {
                        className += " mc-correct";
                    } else if (option === userAnswer) {
                        className += " mc-wrong";
                    }
                } else if (option === userAnswer) {
                    className += " mc-selected";
                }

                return (
                    <button
                        key={index}
                        type="button"
                        className={className}
                        disabled={hasAnswered}
                        onClick={() => {
                            if (!hasAnswered) {
                                setUserAnswer(option);
                            }
                        }}
                    >
                        {option}
                    </button>
                );
            })}
        </div>
    );
}