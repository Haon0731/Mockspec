   
               
        
                          
        
   
import pytest
from flask.helpers import abort


@pytest.mark.parametrize("scenario,has_app_context,code,args,kwargs", [
    (
        "app_context_exists_404",
        True,
        404,
        (),
        {}
    ),
    (
        "app_context_exists_500_with_message",
        True,
        500,
        ("Internal Server Error",),
        {}
    ),
    (
        "no_app_context_403",
        False,
        403,
        (),
        {}
    ),
    (
        "no_app_context_with_args_kwargs",
        False,
        400,
        ("Bad Request",),
        {"description": "Invalid input"}
    ),
    (
        "app_context_custom_code",
        True,
        418,
        (),
        {"description": "I'm a teapot"}
    ),
])
def test_abort_scenarios(mocker, scenario, has_app_context, code, args, kwargs):
       
                    

       
                                              
                                                             
                                          
                                                       
                                                

         
                          
                                     
                                    
       
                                 
                      
    mock_ctx = mocker.MagicMock()

                            
    mock_app = mocker.MagicMock()

                                   
    mock_aborter = mocker.Mock(side_effect=Exception(f"Aborted with {code}"))

                        
                         
    mock_wz_abort = mocker.patch("flask.helpers._wz_abort", side_effect=Exception(f"Werkzeug abort {code}"))

    if has_app_context:
                       
        mock_ctx.app = mock_app
        mock_app.aborter = mock_aborter
                                


                            
    mock_cv_app = mocker.patch("flask.helpers._cv_app")
    mock_cv_app.get.return_value = mock_ctx if has_app_context else None
                      

                                 
    with pytest.raises(Exception) as exc_info:
        abort(code, *args, **kwargs)

                        
    mock_cv_app.get.assert_called_once_with(None)

                       
    if has_app_context:
                          
        mock_aborter.assert_called_once_with(code, *args, **kwargs)
                                                 
        mock_wz_abort.assert_not_called()
        assert f"Aborted with {code}" in str(exc_info.value)
    else:
                             
        mock_aborter.assert_not_called()
        mock_wz_abort.assert_called_once_with(code, *args, **kwargs)
        assert f"Werkzeug abort {code}" in str(exc_info.value)
